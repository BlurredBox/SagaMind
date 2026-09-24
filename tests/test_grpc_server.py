"""Smoke and authorization tests for the optional gRPC surface."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import grpc

from src.config import settings
from src.generated import sagamind_pb2 as pb2
from src.grpc_server import build_servicer


def _context(*metadata: tuple[str, str]) -> MagicMock:
    context = MagicMock()
    context.invocation_metadata.return_value = [SimpleNamespace(key=k, value=v) for k, v in metadata]
    return context


def test_grpc_start_and_status_round_trip():
    coordinator = MagicMock()
    coordinator.get_saga_status.return_value = {
        "saga_id": "saga-1",
        "tenant_id": "tenant-a",
        "goal": "ship safely",
        "status": "RUNNING",
        "completed_steps": ["step-1"],
    }
    servicer = build_servicer(coordinator)

    started = servicer.StartSaga(pb2.StartSagaRequest(tenant_id="tenant-a", goal="ship safely"), _context())
    status = servicer.GetSagaStatus(pb2.SagaStatusRequest(saga_id="saga-1"), _context())

    assert started.status == "RUNNING"
    coordinator.start_transaction_log.assert_called_once_with(started.saga_id, "ship safely", "tenant-a")
    assert status.saga_id == "saga-1"
    assert list(status.completed_steps) == ["step-1"]


def test_grpc_submit_step_maps_request_and_result():
    coordinator = MagicMock()
    coordinator.get_saga_status.return_value = {"tenant_id": "tenant-a"}
    coordinator.execute_saga.return_value = True
    servicer = build_servicer(coordinator)
    request = pb2.StepProposal(
        saga_id="saga-1",
        step_name="safe no-op",
        tool_name="NOOP",
        compensation_tool="NOOP",
        invariants="",
    )

    result = servicer.SubmitStep(request, _context())

    assert result.status == "COMMITTED"
    submitted = coordinator.execute_saga.call_args.args[1][0]
    assert submitted.step_name == "safe no-op"
    assert submitted.action.tool_name == "NOOP"


def test_grpc_unknown_saga_is_not_found():
    coordinator = MagicMock()
    coordinator.get_saga_status.return_value = None
    context = _context()
    servicer = build_servicer(coordinator)

    result = servicer.GetSagaStatus(pb2.SagaStatusRequest(saga_id="missing"), context)

    assert result.saga_id == ""
    context.set_code.assert_called_once_with(grpc.StatusCode.NOT_FOUND)


def test_grpc_tenant_bound_api_key_is_enforced(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", "secret:tenant-a")
    coordinator = MagicMock()
    servicer = build_servicer(coordinator)
    context = _context(("x-api-key", "secret"))

    response = servicer.StartSaga(pb2.StartSagaRequest(tenant_id="tenant-b", goal="denied"), context)

    assert response.saga_id == ""
    context.set_code.assert_called_once_with(grpc.StatusCode.PERMISSION_DENIED)
    coordinator.start_transaction_log.assert_not_called()
