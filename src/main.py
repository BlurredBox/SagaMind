"""
SagaMind Core Runtime API Server
================================

FastAPI gateway exposing the saga transaction lifecycle, memory consolidation/retrieval,
speculative execution, and health/readiness probes.

Security posture
----------------
* API-key authentication is enforced on all mutating/data endpoints whenever keys are
  configured (always in production); ``/health`` and ``/metrics`` are public.
* CORS is restricted to a configured allow-list.
* Request bodies are size-limited.
* Optional per-client rate limiting.
* Per-tool argument schema validation before sandbox execution.
* Request-ID header injected and propagated through all log lines.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.config import settings
from src.logging_config import configure_logging, get_logger
from src.memory.consolidation import MemoryConsolidator
from src.memory.decay import EbbinghausMemoryManager
from src.memory.embedding import EmbeddingService
from src.memory.neo4j_store import Neo4jGraphStore
from src.memory.timescale_store import TimescaleMemoryStore
from src.models import ActionPayload, SagaStep
from src.observability import metrics, span
from src.orchestrator.coordinator import CoordinatorError, SagaTransactionCoordinator
from src.orchestrator.sandbox import WasmSandbox
from src.orchestrator.state_store import SagaStateStore
from src.request_context import request_id
from src.security import PathSecurityError, contain_path, rate_limiter
from src.speculative.orchestrator import SpeculativeOrchestrator
from src.verifier.z3_prover import Z3Verifier

configure_logging()
logger = get_logger("SagaMind.API")

# APScheduler for automatic sleep-cycle consolidation (optional dependency).
_scheduler: Any = None
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    _scheduler = AsyncIOScheduler()
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────────────
# Service singletons
# ─────────────────────────────────────────────────────────────────────

verifier = Z3Verifier()
sandbox = WasmSandbox()
timescale = TimescaleMemoryStore()
neo4j = Neo4jGraphStore()
saga_store = SagaStateStore()
coordinator = SagaTransactionCoordinator(verifier, sandbox, db_client=saga_store)
memory_manager = EbbinghausMemoryManager()
consolidator = MemoryConsolidator(timescale, neo4j)
embedding_service = EmbeddingService()
speculative = SpeculativeOrchestrator(sandbox)


def consolidate_all_tenants() -> None:
    """Run one consolidation cycle for each real tenant in the memory store."""
    for tenant_id in timescale.list_tenants():
        consolidator.run_consolidation_cycle(tenant_id)


# ─────────────────────────────────────────────────────────────────────
# Lifespan
# ─────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    logger.info(
        "SagaMind API starting (env=%s, auth=%s, saga_store=%s).",
        settings.env,
        settings.auth_enabled,
        saga_store.backend,
    )
    try:
        recovered = coordinator.recover()
        if recovered:
            logger.warning("Recovered (rolled back) %d incomplete saga(s) on startup.", recovered)
    except Exception as exc:  # noqa: BLE001
        logger.error("Saga recovery on startup failed: %s", exc)
        if settings.require_backends or settings.is_production:
            raise

    if _scheduler is not None and settings.consolidation_cron:
        try:
            minute, hour, day, month, day_of_week = settings.consolidation_cron.split()
            _scheduler.add_job(
                func=consolidate_all_tenants,
                trigger="cron",
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=day_of_week,
                id="sleep_cycle",
                replace_existing=True,
            )
            _scheduler.start()
            logger.info("Sleep-cycle scheduler started (cron=%s).", settings.consolidation_cron)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to start consolidation scheduler: %s", exc)

    try:
        yield
    finally:
        if _scheduler is not None and _scheduler.running:
            _scheduler.shutdown(wait=False)
        for name, closer in (("Neo4j", neo4j.close), ("saga store", saga_store.close)):
            try:
                closer()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error closing %s on shutdown: %s", name, exc)
        logger.info("SagaMind API shut down cleanly.")


app = FastAPI(
    title="SagaMind Core Runtime API",
    description="Enterprise Multi-Agent Transaction Runtime and Cognitive Memory Engine",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def inject_request_id(request: Request, call_next: Any) -> Any:
    """Stamp every request with a correlation ID visible in all downstream log lines."""
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    token = request_id.set(rid)
    try:
        with span("http.request", method=request.method, route=request.url.path, request_id=rid):
            response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        request_id.reset(token)


@app.middleware("http")
async def limit_body_size(request: Request, call_next: Any) -> Any:
    """Reject oversized request bodies before they are buffered."""
    content_length = request.headers.get("content-length")
    try:
        declared_size = int(content_length) if content_length is not None else None
    except ValueError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Invalid Content-Length header."},
        )
    if declared_size is not None and (declared_size < 0 or declared_size > settings.max_request_bytes):
        return JSONResponse(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            content={"detail": "Request body too large."},
        )
    if content_length is None and request.method in {"POST", "PUT", "PATCH"}:
        body = await request.body()
        if len(body) > settings.max_request_bytes:
            return JSONResponse(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                content={"detail": "Request body too large."},
            )
    return await call_next(request)


# ─────────────────────────────────────────────────────────────────────
# Security dependencies
# ─────────────────────────────────────────────────────────────────────


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if not settings.auth_enabled:
        return
    if not x_api_key or x_api_key not in settings.api_key_set:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )


def enforce_rate_limit(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    client_key = x_api_key or (request.client.host if request.client else "anonymous")
    if not rate_limiter.allow(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Try again shortly.",
        )


def get_bound_tenant(x_api_key: str | None = Header(default=None)) -> str | None:
    """Return the tenant_id bound to this API key (``key:tenant_id`` form), or None if unrestricted."""
    if not x_api_key:
        return None
    return settings.api_key_tenant_map.get(x_api_key)


def enforce_tenant_access(bound_tenant: str | None, tenant_id: str | None) -> None:
    """Raise 403 if the API key is bound to a tenant other than *tenant_id*."""
    if bound_tenant is not None and tenant_id is not None and bound_tenant != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key is not authorized for this tenant.",
        )


_PROTECTED = [Depends(require_api_key), Depends(enforce_rate_limit)]


# ─────────────────────────────────────────────────────────────────────
# Per-tool argument schemas (validation before sandbox execution)
# ─────────────────────────────────────────────────────────────────────


class WriteFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    content: str = ""
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    expected_absent: bool | None = None

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("path must not be empty")
        return v


class DeleteFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    expected_absent: bool | None = None

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("path must not be empty")
        return v


class NoopArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


_ACTION_ARG_SCHEMAS: dict[str, type[BaseModel]] = {
    "WRITE_FILE": WriteFileArgs,
    "NOOP": NoopArgs,
    "DELETE_FILE": DeleteFileArgs,
}

_COMPENSATION_ARG_SCHEMAS: dict[str, type[BaseModel]] = {
    "RESTORE_FILE": BaseModel,
    "DELETE_FILE": DeleteFileArgs,
    "NOOP": NoopArgs,
}


class RestoreFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    existed: bool
    previous: str = Field(max_length=1_000_000)
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    expected_absent: bool | None = None


_COMPENSATION_ARG_SCHEMAS["RESTORE_FILE"] = RestoreFileArgs


def _validate_tool_args(tool_name: str, arguments: dict[str, Any], *, compensation: bool = False) -> dict[str, Any]:
    """Validate tool arguments against the registered schema. Raises HTTPException on failure."""
    schemas = _COMPENSATION_ARG_SCHEMAS if compensation else _ACTION_ARG_SCHEMAS
    if tool_name not in schemas:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown {'compensation' if compensation else 'action'} tool '{tool_name}'. Allowed: {sorted(schemas)}"
            ),
        )
    schema = schemas[tool_name]
    try:
        return schema(**arguments).model_dump(exclude_none=True)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid arguments for tool '{tool_name}': {exc}",
        ) from exc


def _derive_file_contract(tool_name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], ActionPayload]:
    """Capture an exact preimage and bind forward/rollback operations to hashes."""
    try:
        safe_path = Path(contain_path(str(arguments["path"])))
        existed = safe_path.is_file()
        previous = safe_path.read_text(encoding="utf-8") if existed else ""
    except (OSError, UnicodeError, PathSecurityError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if len(previous) > 1_000_000:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="File is too large for the built-in exact rollback contract.",
        )
    before_hash = hashlib.sha256(previous.encode()).hexdigest() if existed else None
    guarded = dict(arguments)
    guarded.pop("expected_sha256", None)
    guarded.pop("expected_absent", None)
    if existed:
        guarded["expected_sha256"] = before_hash
    else:
        guarded["expected_absent"] = True
    compensation_args: dict[str, Any] = {
        "path": str(safe_path),
        "existed": existed,
        "previous": previous,
    }
    if tool_name == "WRITE_FILE":
        compensation_args["expected_sha256"] = hashlib.sha256(guarded["content"].encode()).hexdigest()
    else:
        compensation_args["expected_absent"] = True
    return guarded, ActionPayload("RESTORE_FILE", compensation_args)


# ─────────────────────────────────────────────────────────────────────
# Request / Response schemas
# ─────────────────────────────────────────────────────────────────────


class StartSagaRequest(BaseModel):
    tenant_id: str
    goal: str


class MemoryIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=50)
    agent_role: str = Field(min_length=1, max_length=50)
    summary: str = Field(min_length=1, max_length=20_000)
    importance: float = Field(ge=0.0, le=1.0)
    context: dict[str, Any] = Field(default_factory=dict)


class StepProposal(BaseModel):
    saga_id: str
    step_name: str
    tool_name: str
    arguments: dict[str, Any]
    compensation_tool: str | None = None
    compensation_arguments: dict[str, Any] = Field(default_factory=dict)
    invariants: str
    idempotency_key: str | None = None
    requires_approval: bool = False


class DraftProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class SpeculativeRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=50)
    goal: str = Field(default="speculative action", min_length=1, max_length=500)
    drafts: list[DraftProposal]


class HealthResponse(BaseModel):
    status: str
    environment: str
    version: str
    backends: dict[str, str] | None = None


# ─────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    """Process liveness probe. Use ``/ready`` for dependency readiness."""
    return HealthResponse(
        status="HEALTHY",
        environment=settings.env,
        version="1.0.0",
        backends=_backend_status(),
    )


def _backend_status() -> dict[str, str]:
    """Return truthful backend and execution-boundary modes."""
    return {
        "timescale": "live" if getattr(timescale, "pool_active", False) else "fallback",
        "neo4j": "live" if getattr(neo4j, "active", False) else "fallback",
        "verifier": "z3" if getattr(verifier, "z3_active", False) else "semantic-fallback",
        "isolation": "isolated-worker" if sandbox.execution_mode == "isolated" else "host-development-fallback",
        "wasi": "live" if getattr(sandbox, "engine", None) else "unavailable",
        "wasm": "live" if getattr(sandbox, "engine", None) else "unavailable",
        "saga_store": saga_store.backend,
    }


@app.get("/ready", response_model=HealthResponse)
def readiness_check(response: Response) -> HealthResponse:
    """Dependency readiness probe; production never accepts fallback backends."""
    backends = _backend_status()
    required = (
        backends["timescale"] == "live"
        and backends["neo4j"] == "live"
        and backends["verifier"] == "z3"
        and backends["isolation"] == "isolated-worker"
        and backends["saga_store"] == "postgres"
    )
    ready = required if (settings.is_production or settings.require_backends) else True
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="READY" if ready else "NOT_READY",
        environment=settings.env,
        version="1.0.0",
        backends=backends,
    )


@app.get("/health/backends", response_model=HealthResponse, include_in_schema=False)
def backend_health_details() -> HealthResponse:
    """Backward-compatible backend detail endpoint for operators."""
    backends = {
        **_backend_status(),
    }
    return HealthResponse(
        status="HEALTHY",
        environment=settings.env,
        version="1.0.0",
        backends=backends,
    )


@app.get("/metrics")
def prometheus_metrics() -> Response:
    """Prometheus scrape endpoint (public)."""
    payload, content_type = metrics.exposition()
    return Response(content=payload, media_type=content_type)


def _get_saga_or_404(saga_id: str) -> Any:
    saga = coordinator.active_sagas.get(saga_id)
    if saga is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saga not found.")
    return saga


@app.post("/saga/start", dependencies=_PROTECTED)
def start_saga(payload: StartSagaRequest, bound_tenant: str | None = Depends(get_bound_tenant)) -> dict[str, str]:
    """Initialize a new saga transaction session."""
    enforce_tenant_access(bound_tenant, payload.tenant_id)
    saga_id = str(uuid.uuid4())
    coordinator.start_transaction_log(saga_id, payload.goal, payload.tenant_id)
    return {"saga_id": saga_id, "status": "RUNNING"}


@app.post("/saga/step", dependencies=_PROTECTED)
def submit_step(payload: StepProposal, bound_tenant: str | None = Depends(get_bound_tenant)) -> dict[str, str]:
    """Submit and execute a single saga step through the verification gate."""
    saga = _get_saga_or_404(payload.saga_id)
    enforce_tenant_access(bound_tenant, saga.tenant_id)

    action_arguments = _validate_tool_args(payload.tool_name, payload.arguments)
    if payload.tool_name in {"WRITE_FILE", "DELETE_FILE"}:
        action_arguments, compensation = _derive_file_contract(payload.tool_name, action_arguments)
    else:
        compensation_tool = payload.compensation_tool or "NOOP"
        compensation_arguments = _validate_tool_args(
            compensation_tool,
            payload.compensation_arguments,
            compensation=True,
        )
        compensation = ActionPayload(compensation_tool, compensation_arguments)

    step = SagaStep(
        step_id=str(uuid.uuid4()),
        step_name=payload.step_name,
        action=ActionPayload(payload.tool_name, action_arguments),
        compensation=compensation,
        invariants=payload.invariants,
        idempotency_key=payload.idempotency_key,
        requires_approval=payload.requires_approval,
    )

    try:
        success = coordinator.execute_saga(payload.saga_id, [step])
    except CoordinatorError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if saga.status == "AWAITING_APPROVAL":
        return {"status": "AWAITING_APPROVAL", "step_id": step.step_id}

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Transaction step validation failed. Saga rolled back.",
                "step_error": step.error,
            },
        )
    return {"status": "COMMITTED", "step_id": step.step_id}


@app.get("/saga/{saga_id}/status", dependencies=_PROTECTED)
def saga_status(saga_id: str, bound_tenant: str | None = Depends(get_bound_tenant)) -> dict[str, Any]:
    """Return the current state of a saga transaction."""
    state = coordinator.get_saga_status(saga_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saga not found.")
    enforce_tenant_access(bound_tenant, state["tenant_id"])
    return state


@app.post("/saga/{saga_id}/approve", dependencies=_PROTECTED)
def approve_saga_step(saga_id: str, bound_tenant: str | None = Depends(get_bound_tenant)) -> dict[str, str]:
    """Approve the step currently awaiting human-in-the-loop approval and resume execution."""
    saga = _get_saga_or_404(saga_id)
    enforce_tenant_access(bound_tenant, saga.tenant_id)
    try:
        success = coordinator.resume_after_approval(saga_id)
    except CoordinatorError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": "COMMITTED" if success else saga.status}


@app.post("/saga/{saga_id}/reject", dependencies=_PROTECTED)
def reject_saga_step(saga_id: str, bound_tenant: str | None = Depends(get_bound_tenant)) -> dict[str, str]:
    """Reject the step awaiting approval, rolling back any already-committed steps."""
    saga = _get_saga_or_404(saga_id)
    enforce_tenant_access(bound_tenant, saga.tenant_id)
    try:
        coordinator.reject_pending(saga_id)
    except CoordinatorError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": "ROLLED_BACK"}


@app.get("/saga/{saga_id}/history", dependencies=_PROTECTED)
def saga_history(saga_id: str, bound_tenant: str | None = Depends(get_bound_tenant)) -> dict[str, Any]:
    """Return the ordered forward-execution history of a saga for replay/time-travel debugging."""
    saga = _get_saga_or_404(saga_id)
    enforce_tenant_access(bound_tenant, saga.tenant_id)
    history = saga_store.get_history(saga_id) if hasattr(saga_store, "get_history") else []
    return {"saga_id": saga_id, "history": history}


@app.get("/saga/{saga_id}/stream", dependencies=_PROTECTED)
async def stream_saga(saga_id: str, bound_tenant: str | None = Depends(get_bound_tenant)) -> Any:
    """Stream saga status updates as Server-Sent Events until a terminal state is reached."""
    import asyncio
    import json as _json

    from fastapi.responses import StreamingResponse

    saga = _get_saga_or_404(saga_id)
    enforce_tenant_access(bound_tenant, saga.tenant_id)

    terminal = {"COMMITTED", "ROLLED_BACK", "FAILED", "COMPENSATION_FAILED"}

    async def event_source() -> AsyncGenerator[str]:
        last_status: str | None = None
        while True:
            state = coordinator.get_saga_status(saga_id)
            if state is None:
                break
            if state["status"] != last_status:
                yield f"data: {_json.dumps(state)}\n\n"
                last_status = state["status"]
            if state["status"] in terminal:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_source(), media_type="text/event-stream")


@app.get("/saga/dead-letters", dependencies=_PROTECTED)
def list_dead_letters(bound_tenant: str | None = Depends(get_bound_tenant)) -> dict[str, Any]:
    """Return sagas that reached COMPENSATION_FAILED and require manual operator resolution."""
    if not hasattr(saga_store, "list_dead_letters"):
        return {"dead_letters": []}
    return {"dead_letters": saga_store.list_dead_letters(bound_tenant)}


@app.post("/memory/consolidate", dependencies=_PROTECTED)
def run_consolidation(
    tenant_id: str,
    background_tasks: BackgroundTasks,
    bound_tenant: str | None = Depends(get_bound_tenant),
) -> dict[str, str]:
    """Trigger an asynchronous memory consolidation sleep-cycle."""
    enforce_tenant_access(bound_tenant, tenant_id)
    background_tasks.add_task(consolidator.run_consolidation_cycle, tenant_id)
    return {"status": "QUEUED", "message": "Asynchronous sleep cycle triggered."}


@app.post("/memory", dependencies=_PROTECTED, status_code=status.HTTP_201_CREATED)
def ingest_memory(
    payload: MemoryIngestRequest,
    bound_tenant: str | None = Depends(get_bound_tenant),
) -> dict[str, str]:
    """Create a tenant-scoped episodic memory with a generated embedding."""
    enforce_tenant_access(bound_tenant, payload.tenant_id)
    memory_id = str(uuid.uuid4())
    timescale.write_episodic_memory(
        memory_id,
        payload.tenant_id,
        payload.agent_role,
        payload.summary,
        payload.importance,
        embedding_service.embed(payload.summary),
        payload.context,
    )
    return {"memory_id": memory_id, "status": "CREATED"}


@app.get("/memory/active", dependencies=_PROTECTED)
def get_active_memories(
    tenant_id: str,
    query: str | None = None,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    bound_tenant: str | None = Depends(get_bound_tenant),
) -> dict[str, Any]:
    """Retrieve active (non-evicted) memories for a tenant, optionally ranked by *query*.

    Retention filtering (Ebbinghaus decay) is pushed into SQL (§4.2). Each memory is
    augmented with related concepts surfaced from the semantic graph (§6.4 GraphRAG).
    """
    enforce_tenant_access(bound_tenant, tenant_id)
    query_vector = embedding_service.embed(query) if query else [0.0] * settings.embedding_dim
    active = timescale.retrieve_active_memories(
        tenant_id,
        query_vector,
        s_init=memory_manager.s_init,
        gamma=memory_manager.gamma,
        tau=memory_manager.tau,
        limit=limit,
        offset=offset,
    )
    timescale.mark_retrieved([str(memory["memory_id"]) for memory in active])
    retrieved_at = datetime.now(timezone.utc)
    for memory in active:
        memory["retrieval_count"] = int(memory["retrieval_count"]) + 1
        memory["last_retrieved_at"] = retrieved_at

    for m in active:
        neighbors = neo4j.get_neighbors(m["agent_role"])
        m["related_concepts"] = [n["target"] for n in neighbors if n.get("type") == "DISCOVERED_CONCEPT"]

    return {
        "active_memories": active,
        "limit": limit,
        "offset": offset,
        "count": len(active),
    }


@app.post("/speculative/run", dependencies=_PROTECTED)
async def run_speculative(
    payload: SpeculativeRequest,
    bound_tenant: str | None = Depends(get_bound_tenant),
) -> dict[str, Any]:
    """Validate drafts concurrently, then commit the winner through a durable saga."""
    enforce_tenant_access(bound_tenant, payload.tenant_id)
    drafts = [d.model_dump() for d in payload.drafts]
    results = await speculative.run_speculative_drafts(drafts)
    selected = speculative.select_winner(results)
    if selected is None:
        return {"results": results, "selected_sandbox": None, "status": "NO_VALID_DRAFT"}
    sandbox_id, action = selected
    action_arguments = _validate_tool_args(action.tool_name, action.arguments)
    if action.tool_name in {"WRITE_FILE", "DELETE_FILE"}:
        action_arguments, compensation = _derive_file_contract(action.tool_name, action_arguments)
    else:
        compensation = ActionPayload("NOOP", {})
    saga_id = str(uuid.uuid4())
    coordinator.start_transaction_log(saga_id, payload.goal, payload.tenant_id)
    step = SagaStep(
        step_id=str(uuid.uuid4()),
        step_name=f"speculative winner {sandbox_id}",
        action=ActionPayload(action.tool_name, action_arguments),
        compensation=compensation,
        invariants="",
        idempotency_key=f"speculative:{sandbox_id}",
    )
    success = coordinator.execute_saga(saga_id, [step])
    if not success:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "Winning draft failed during journaled commit.", "saga_id": saga_id},
        )
    return {
        "results": results,
        "selected_sandbox": sandbox_id,
        "saga_id": saga_id,
        "status": "COMMITTED",
    }


# ─────────────────────────────────────────────────────────────────────
# Server entry point
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Starting SagaMind API on %s:%s", settings.host, settings.port)
    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        reload=(settings.env == "development"),
        log_level="info",
    )
