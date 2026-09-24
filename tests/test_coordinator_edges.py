"""Focused edge tests for coordinator observability and fail-closed recovery."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.models import SagaStatus
from src.orchestrator.coordinator import CoordinatorError, SagaTransactionCoordinator


def test_status_snapshot_and_unknown(coordinator):
    assert coordinator.get_saga_status("missing") is None
    coordinator.start_transaction_log("visible", "inspect", "tenant")

    status = coordinator.get_saga_status("visible")

    assert status is not None
    assert status["goal"] == "inspect"
    assert status["completed_steps"] == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, "APPLIED"),
        (False, "NOT_APPLIED"),
        ("applied", "APPLIED"),
        ("not_applied", "NOT_APPLIED"),
        (None, "UNKNOWN"),
    ],
)
def test_resolution_normalization(value, expected):
    assert SagaTransactionCoordinator._normalise_resolution(value) == expected


def test_resolution_without_observer_is_unknown(mock_verifier):
    coordinator = SagaTransactionCoordinator(mock_verifier, object(), db_client=MagicMock())
    effect = {"saga_id": "s", "idempotency_key": None}

    assert coordinator._resolve_forward_effect(effect) == "UNKNOWN"
    assert coordinator._resolve_compensation(effect) == "UNKNOWN"


def test_result_data_handles_dict_typed_and_status_only_results():
    assert SagaTransactionCoordinator._result_data({"x": 1}) == {"x": 1}
    assert SagaTransactionCoordinator._result_data(SimpleNamespace(data={"x": 2})) == {"x": 2}
    assert SagaTransactionCoordinator._result_data(SimpleNamespace(status="DONE")) == {"status": "DONE"}


def test_approval_operations_reject_unknown_and_wrong_state(coordinator):
    with pytest.raises(CoordinatorError, match="not found"):
        coordinator.resume_after_approval("missing")
    with pytest.raises(CoordinatorError, match="not found"):
        coordinator.reject_pending("missing")
    coordinator.start_transaction_log("running", "goal", "tenant")
    assert coordinator.active_sagas["running"].status == SagaStatus.RUNNING.value
    with pytest.raises(CoordinatorError, match="not awaiting"):
        coordinator.resume_after_approval("running")
    with pytest.raises(CoordinatorError, match="not awaiting"):
        coordinator.reject_pending("running")


def test_legacy_recovery_exception_is_dead_lettered(mock_verifier):
    store = MagicMock()
    store.list_incomplete.return_value = [
        {"saga_id": "broken", "compensations": [{"tool_name": "UNDO", "arguments": {}}]}
    ]
    sandbox = MagicMock()
    sandbox.execute_compensation.side_effect = RuntimeError("resolver exploded")
    coordinator = SagaTransactionCoordinator(mock_verifier, sandbox, db_client=store)

    assert coordinator.recover() == 0
    store.write_transaction_state.assert_called_with(
        "broken",
        SagaStatus.COMPENSATION_FAILED.value,
        {"recovered": False, "error": "resolver exploded"},
    )
    store.push_dead_letter.assert_called_once()


def test_preflight_failure_never_marks_effect_as_started(mock_verifier):
    store = MagicMock()
    sandbox = MagicMock()
    sandbox.validate.side_effect = RuntimeError("policy rejected")
    coordinator = SagaTransactionCoordinator(mock_verifier, sandbox, db_client=store)
    coordinator.start_transaction_log("preflight", "reject unsafe action", "tenant")
    step = SimpleNamespace(
        step_id="step-1",
        step_name="unsafe",
        action=SimpleNamespace(tool_name="UNKNOWN", arguments={}),
        compensation=SimpleNamespace(tool_name="NOOP", arguments={}),
        invariants="",
        status="PENDING",
        error="",
        idempotency_key=None,
        requires_approval=False,
        approved=False,
    )

    assert coordinator.execute_saga("preflight", [step]) is False
    store.prepare_effect.assert_not_called()
    store.transition_effect.assert_not_called()
    store.push_dead_letter.assert_not_called()
