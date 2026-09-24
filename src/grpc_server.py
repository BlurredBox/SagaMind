"""
SagaMind gRPC Gateway
=====================

Async private gRPC subset for low-latency service-to-service transaction calls.

Generated stubs live in ``src/generated`` and are committed with the schema. Regenerate
them with ``scripts/gen_proto.sh`` after changing ``proto/sagamind.proto``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.logging_config import configure_logging, get_logger
from src.memory.consolidation import MemoryConsolidator
from src.memory.neo4j_store import Neo4jGraphStore
from src.memory.timescale_store import TimescaleMemoryStore
from src.models import ActionPayload, SagaStep
from src.orchestrator.coordinator import SagaTransactionCoordinator
from src.orchestrator.sandbox import WasmSandbox
from src.orchestrator.state_store import SagaStateStore
from src.verifier.z3_prover import Z3Verifier

logger = get_logger("SagaMind.gRPC")

_CODEGEN_HINT = (
    'gRPC stubs not found. Run `pip install -e ".[grpc]"` then `./scripts/gen_proto.sh` '
    "to generate src/generated/sagamind_pb2*.py."
)


def _load_stubs() -> tuple[Any, Any]:
    try:
        from src.generated import sagamind_pb2, sagamind_pb2_grpc
    except ImportError as exc:  # pragma: no cover - exercised only without codegen
        raise RuntimeError(_CODEGEN_HINT) from exc
    return sagamind_pb2, sagamind_pb2_grpc


def build_servicer(coordinator: SagaTransactionCoordinator) -> Any:
    """Construct the gRPC servicer bound to a coordinator (factory keeps imports lazy)."""
    pb2, pb2_grpc = _load_stubs()
    import uuid

    class SagaMindServicer(pb2_grpc.SagaMindServicer):  # type: ignore[misc, name-defined]
        @staticmethod
        def _authorize(context: Any, tenant_id: str | None) -> bool:
            from src.config import settings

            if not settings.auth_enabled:
                return True
            metadata = {item.key.lower(): item.value for item in context.invocation_metadata()}
            key = metadata.get("x-api-key")
            bound = settings.api_key_tenant_map.get(key or "")
            allowed = bool(key and key in settings.api_key_set and (bound is None or bound == tenant_id))
            if not allowed:
                context.set_code(__import__("grpc").StatusCode.PERMISSION_DENIED)
                context.set_details("Invalid API key or tenant binding.")
            return allowed

        def StartSaga(self, request: Any, context: Any) -> Any:  # noqa: N802 - gRPC RPC name
            if not self._authorize(context, request.tenant_id):
                return pb2.StartSagaResponse()
            saga_id = str(uuid.uuid4())
            coordinator.start_transaction_log(saga_id, request.goal, request.tenant_id)
            return pb2.StartSagaResponse(saga_id=saga_id, status="RUNNING")

        def SubmitStep(self, request: Any, context: Any) -> Any:  # noqa: N802 - gRPC RPC name
            state = coordinator.get_saga_status(request.saga_id)
            if state is None:
                context.set_code(__import__("grpc").StatusCode.NOT_FOUND)
                context.set_details("Saga not found.")
                return pb2.StepResult(error="Saga not found.")
            if not self._authorize(context, state["tenant_id"]):
                return pb2.StepResult(error="Unauthorized.")
            step = _proposal_to_step(pb2, request)
            ok = coordinator.execute_saga(request.saga_id, [step])
            return pb2.StepResult(
                status="COMMITTED" if ok else "ROLLED_BACK",
                step_id=step.step_id,
                error=step.error,
            )

        def GetSagaStatus(self, request: Any, context: Any) -> Any:  # noqa: N802 - gRPC RPC name
            state = coordinator.get_saga_status(request.saga_id)
            if state is None:
                context.set_code(__import__("grpc").StatusCode.NOT_FOUND)
                context.set_details("Saga not found.")
                return pb2.SagaStatusResponse()
            if not self._authorize(context, state["tenant_id"]):
                return pb2.SagaStatusResponse()
            return pb2.SagaStatusResponse(
                saga_id=state["saga_id"],
                tenant_id=state["tenant_id"],
                goal=state["goal"],
                status=state["status"],
                completed_steps=state["completed_steps"],
            )

        def StreamSteps(self, request: Any, context: Any) -> Any:  # noqa: N802 - gRPC RPC name
            state = coordinator.get_saga_status(request.saga_id)
            if state is None:
                context.set_code(__import__("grpc").StatusCode.NOT_FOUND)
                context.set_details("Saga not found.")
                return
            if not self._authorize(context, state["tenant_id"]):
                return
            step = _proposal_to_step(pb2, request)
            events: list[Any] = []

            def emit(s: SagaStep, st: str, err: str) -> None:
                events.append(pb2.StepEvent(step_name=s.step_name, status=st, error=err, timestamp=time.time()))

            coordinator.execute_saga(request.saga_id, [step], callback=emit)
            yield from events

    return SagaMindServicer()


def _proposal_to_step(pb2: Any, request: Any) -> SagaStep:
    import uuid

    return SagaStep(
        step_id=str(uuid.uuid4()),
        step_name=request.step_name,
        action=ActionPayload(request.tool_name, dict(request.arguments)),
        compensation=ActionPayload(request.compensation_tool, dict(request.compensation_arguments)),
        invariants=request.invariants,
    )


async def serve(port: int | None = None) -> None:
    """Start the async gRPC server (blocks until terminated)."""
    import grpc

    from src.config import settings

    _pb2, pb2_grpc = _load_stubs()
    port = port or settings.grpc_port

    verifier = Z3Verifier()
    sandbox = WasmSandbox()
    timescale = TimescaleMemoryStore()
    neo4j = Neo4jGraphStore()
    _consolidator = MemoryConsolidator(timescale, neo4j)
    saga_store = SagaStateStore()
    coordinator = SagaTransactionCoordinator(verifier, sandbox, db_client=saga_store)
    coordinator.recover()

    server = grpc.aio.server()
    pb2_grpc.add_SagaMindServicer_to_server(build_servicer(coordinator), server)
    if settings.grpc_tls_cert and settings.grpc_tls_key:
        with open(settings.grpc_tls_key, "rb") as key_file, open(settings.grpc_tls_cert, "rb") as cert_file:
            credentials = grpc.ssl_server_credentials(((key_file.read(), cert_file.read()),))
        server.add_secure_port(f"[::]:{port}", credentials)
        transport = "TLS"
    elif settings.is_production:
        raise RuntimeError("Production gRPC requires GRPC_TLS_CERT and GRPC_TLS_KEY.")
    else:
        server.add_insecure_port(f"[::]:{port}")
        transport = "insecure-development"
    logger.info("SagaMind gRPC server listening on :%d (%s)", port, transport)
    await server.start()
    await server.wait_for_termination()


if __name__ == "__main__":
    import asyncio

    configure_logging()
    logging.getLogger().setLevel(logging.INFO)
    asyncio.run(serve())
