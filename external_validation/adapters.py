"""Adapters for SagaMind and versioned public workflow frameworks.

The v2 comparison exposes each public framework's native pattern and a second
configuration with matched application controls. This prevents a capability
mismatch from being presented as a general framework ranking.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict
from uuid import uuid4

from src.config import settings
from src.models import ActionPayload, SagaStep
from src.orchestrator.coordinator import SagaTransactionCoordinator
from src.orchestrator.sandbox import ToolDefinition, WasmSandbox
from src.orchestrator.state_store import SagaStateStore
from src.verifier.z3_prover import Z3Verifier


class _InjectedFailureSandbox:
    """Delegate real tools to SagaMind and expose one benchmark-only fault."""

    def __init__(self, delegate: WasmSandbox) -> None:
        self.delegate = delegate

    def validate(self, action: ActionPayload) -> ToolDefinition | None:
        if action.tool_name == "INJECT_FAILURE":
            return None
        return self.delegate.validate(action)

    def execute(self, action: ActionPayload):
        if action.tool_name == "INJECT_FAILURE":
            raise RuntimeError("injected workload failure")
        return self.delegate.execute(action)

    def execute_compensation(self, action: ActionPayload) -> bool:
        return self.delegate.execute_compensation(action)


def _path_invariant(workspace: str) -> str:
    escaped = workspace.replace('"', '""')
    return f'(assert (str.prefixof "{escaped}/" path))'


def run_sagamind_integrated(operations: list[dict[str, Any]], case_id: str) -> bool:
    """Exercise coordinator, Z3, typed policy, journal, and isolated worker."""
    previous_root = settings.allowed_workspace_root
    previous_backend = settings.state_store_backend
    settings.allowed_workspace_root = str(operations[0]["workspace"])
    settings.state_store_backend = "memory"
    store: SagaStateStore | None = None
    try:
        sandbox = _InjectedFailureSandbox(WasmSandbox(execution_mode="isolated", allow_host_fallback=False))
        store = SagaStateStore()
        coordinator = SagaTransactionCoordinator(Z3Verifier(), sandbox, db_client=store)
        coordinator.start_transaction_log(case_id, "external validation", "replicator")
        steps: list[SagaStep] = []
        for index, operation in enumerate(operations):
            failing = operation["op"] == "fail"
            action = (
                ActionPayload("INJECT_FAILURE", {})
                if failing
                else ActionPayload(
                    "WRITE_FILE",
                    {"path": operation["path"], "content": operation["content"]},
                )
            )
            compensation = (
                ActionPayload("NOOP", {})
                if failing
                else ActionPayload(
                    "RESTORE_FILE",
                    {
                        "path": operation["path"],
                        "existed": operation["existed"],
                        "previous": operation["previous"],
                    },
                )
            )
            steps.append(
                SagaStep(
                    step_id=f"{case_id}-{index}",
                    step_name=f"operation-{index}",
                    action=action,
                    compensation=compensation,
                    invariants="" if failing else _path_invariant(str(operation["workspace"])),
                )
            )
        return coordinator.execute_saga(case_id, steps)
    finally:
        if store is not None:
            store.close()
        settings.allowed_workspace_root = previous_root
        settings.state_store_backend = previous_backend


class _GraphState(TypedDict):
    operations: list[dict[str, Any]]
    cursor: int


def _write(operation: dict[str, Any], *, guard_path: bool) -> None:
    if operation["op"] == "fail":
        raise RuntimeError("injected workload failure")
    path = Path(str(operation["path"]))
    if guard_path:
        workspace = Path(str(operation["workspace"]))
        try:
            path.relative_to(workspace)
        except ValueError as exc:
            raise RuntimeError("path policy rejected") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(operation["content"]), encoding="utf-8")


def _compensate(operation: dict[str, Any]) -> None:
    path = Path(str(operation["path"]))
    if bool(operation["existed"]):
        path.write_text(str(operation["previous"]), encoding="utf-8")
    elif path.exists():
        path.unlink()


def _run_langgraph(operations: list[dict[str, Any]], case_id: str, *, matched_controls: bool) -> bool:
    from langgraph.checkpoint.memory import InMemorySaver  # type: ignore[import-not-found]
    from langgraph.graph import END, START, StateGraph  # type: ignore[import-not-found]

    graph = StateGraph(_GraphState)
    previous = START
    for index in range(len(operations)):
        node_name = f"operation_{index}"

        def execute(state: _GraphState, position: int = index) -> dict[str, int]:
            _write(state["operations"][position], guard_path=matched_controls)
            return {"cursor": position + 1}

        graph.add_node(node_name, execute)
        graph.add_edge(previous, node_name)
        previous = node_name
    graph.add_edge(previous, END)
    app = graph.compile(checkpointer=InMemorySaver())
    try:
        app.invoke(
            {"operations": operations, "cursor": 0},
            {"configurable": {"thread_id": case_id}},
        )
        return True
    except RuntimeError as exc:
        if str(exc) not in {"injected workload failure", "path policy rejected"}:
            raise
        if matched_controls:
            for operation in reversed(operations):
                if operation["op"] != "fail":
                    _compensate(operation)
        return False


def run_langgraph_native(operations: list[dict[str, Any]], case_id: str) -> bool:
    return _run_langgraph(operations, case_id, matched_controls=False)


def run_langgraph_matched(operations: list[dict[str, Any]], case_id: str) -> bool:
    return _run_langgraph(operations, case_id, matched_controls=True)


class TemporalRunner:
    """One ephemeral Temporal test server and worker for the whole corpus."""

    def __init__(self) -> None:
        self.environment: Any = None
        self.worker: Any = None
        self.task_queue = f"sagamind-validation-{uuid4()}"

    async def __aenter__(self) -> TemporalRunner:
        try:
            from external_validation.temporal_baseline import (
                TemporalSagaWorkflow,
                temporal_apply,
                temporal_compensate,
            )
        except ImportError as exc:
            raise RuntimeError("temporalio is not installed; install external_validation/requirements.lock") from exc
        from temporalio.testing import WorkflowEnvironment
        from temporalio.worker import Worker

        self.environment = await WorkflowEnvironment.start_time_skipping()
        self.worker = Worker(
            self.environment.client,
            task_queue=self.task_queue,
            workflows=[TemporalSagaWorkflow],
            activities=[temporal_apply, temporal_compensate],
        )
        await self.worker.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.worker is not None:
            await self.worker.__aexit__(exc_type, exc, traceback)
        if self.environment is not None:
            await self.environment.__aexit__(exc_type, exc, traceback)

    async def _run(self, operations: list[dict[str, Any]], case_id: str, *, guarded: bool) -> bool:
        from external_validation.temporal_baseline import TemporalSagaWorkflow

        configured = [{**operation, "guard_path": guarded} for operation in operations]
        result = await self.environment.client.execute_workflow(
            TemporalSagaWorkflow.run,
            configured,
            id=f"{case_id}-{uuid4()}",
            task_queue=self.task_queue,
        )
        return bool(result)

    async def run_native(self, operations: list[dict[str, Any]], case_id: str) -> bool:
        return await self._run(operations, case_id, guarded=False)

    async def run_guarded(self, operations: list[dict[str, Any]], case_id: str) -> bool:
        return await self._run(operations, case_id, guarded=True)
