"""Crash-consistency tests for the write-ahead effect journal."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from src.models import ActionPayload, SagaStep
from src.orchestrator.coordinator import InjectedCrash, SagaTransactionCoordinator
from src.orchestrator.state_store import EffectState, JournalConflictError, SagaStateStore


class PassingVerifier:
    def verify(self, _arguments: dict[str, Any], _invariants: str) -> tuple[bool, str]:
        return True, "OK"


@dataclass
class ObservableSandbox:
    applied: set[str] = field(default_factory=set)
    executions: int = 0
    compensations: int = 0
    idempotent_executions: int = 0

    def execute(self, action: ActionPayload) -> dict[str, str]:
        self.executions += 1
        self.applied.add(action.arguments["resource"])
        return {"status": "SUCCESS"}

    def execute_compensation(self, action: ActionPayload) -> bool:
        self.compensations += 1
        self.applied.discard(action.arguments["resource"])
        return True

    def resolve_effect(self, effect: dict[str, Any]) -> str:
        resource = effect["action"]["arguments"]["resource"]
        return "APPLIED" if resource in self.applied else "NOT_APPLIED"

    def resolve_compensation(self, effect: dict[str, Any]) -> str:
        resource = effect["action"]["arguments"]["resource"]
        return "APPLIED" if resource not in self.applied else "NOT_APPLIED"

    def execute_idempotent(self, action: ActionPayload, _key: str) -> dict[str, str]:
        self.idempotent_executions += 1
        self.applied.add(action.arguments["resource"])
        return {"status": "SUCCESS"}


def _step(step_id: str = "step-1", *, key: str | None = "idem-1") -> SagaStep:
    return SagaStep(
        step_id=step_id,
        step_name=f"write {step_id}",
        action=ActionPayload("CREATE", {"resource": step_id}),
        compensation=ActionPayload("DELETE", {"resource": step_id}),
        invariants="(assert true)",
        idempotency_key=key,
    )


def _crash_at(boundary: str):
    def inject(name: str, _context: dict[str, Any]) -> None:
        if name == boundary:
            raise InjectedCrash(boundary)

    return inject


@pytest.mark.parametrize(
    ("boundary", "initial_state", "effect_was_applied", "expected_final_state"),
    [
        ("effect_prepared", EffectState.PREPARED.value, False, EffectState.ABORTED.value),
        ("effect_executing", EffectState.EXECUTING.value, False, EffectState.ABORTED.value),
        ("effect_executed", EffectState.EXECUTING.value, True, EffectState.COMPENSATED.value),
        ("effect_applied", EffectState.APPLIED.value, True, EffectState.COMPENSATED.value),
        ("effect_committed", EffectState.COMMITTED.value, True, EffectState.COMPENSATED.value),
    ],
)
def test_recovery_at_every_forward_boundary(
    boundary: str,
    initial_state: str,
    effect_was_applied: bool,
    expected_final_state: str,
) -> None:
    store = SagaStateStore()
    sandbox = ObservableSandbox()
    coordinator = SagaTransactionCoordinator(
        PassingVerifier(), sandbox, db_client=store, fault_injector=_crash_at(boundary)
    )
    coordinator.start_transaction_log("saga-forward", "fault injection", "tenant")

    with pytest.raises(InjectedCrash):
        coordinator.execute_saga("saga-forward", [_step()])

    assert store.get_effect("saga-forward", "step-1")["state"] == initial_state
    assert ("step-1" in sandbox.applied) is effect_was_applied

    restarted = SagaTransactionCoordinator(PassingVerifier(), sandbox, db_client=store)
    assert restarted.recover() == 1
    assert store.get_effect("saga-forward", "step-1")["state"] == expected_final_state
    assert sandbox.applied == set()
    assert store.list_dead_letters() == []


@pytest.mark.parametrize(
    ("boundary", "effect_present", "expected_compensations"),
    [
        ("compensation_executing", True, 1),
        ("compensation_executed", False, 1),
        ("compensation_recorded", False, 1),
    ],
)
def test_recovery_at_every_compensation_boundary(
    boundary: str,
    effect_present: bool,
    expected_compensations: int,
) -> None:
    store = SagaStateStore()
    sandbox = ObservableSandbox()
    coordinator = SagaTransactionCoordinator(
        PassingVerifier(), sandbox, db_client=store, fault_injector=_crash_at(boundary)
    )
    coordinator.start_transaction_log("saga-comp", "fault injection", "tenant")
    step = _step()
    assert coordinator.execute_saga("saga-comp", [step]) is True

    # Re-open the saga solely to drive the public compensation path, like a later
    # orchestration step failing after this effect committed.
    store.write_transaction_state("saga-comp", "RUNNING", {})
    with pytest.raises(InjectedCrash):
        coordinator.execute_compensations("saga-comp", [step])

    assert ("step-1" in sandbox.applied) is effect_present
    restarted = SagaTransactionCoordinator(PassingVerifier(), sandbox, db_client=store)
    assert restarted.recover() == 1
    assert store.get_effect("saga-comp", "step-1")["state"] == EffectState.COMPENSATED.value
    assert sandbox.applied == set()
    assert sandbox.compensations == expected_compensations


def test_unresolvable_executing_effect_is_dead_lettered_not_guessed() -> None:
    class NoResolverSandbox:
        def execute_compensation(self, _action: ActionPayload) -> bool:
            pytest.fail("ambiguous effects must not be blindly compensated")

    store = SagaStateStore()
    store.write_transaction_state("saga-ambiguous", "RUNNING", {})
    store.prepare_effect("saga-ambiguous", "s1", "write", "CREATE", {}, "DELETE", {})
    store.transition_effect("saga-ambiguous", "s1", EffectState.PREPARED.value, EffectState.EXECUTING.value)

    restarted = SagaTransactionCoordinator(PassingVerifier(), NoResolverSandbox(), db_client=store)
    assert restarted.recover() == 0
    assert store.get_effect("saga-ambiguous", "s1")["state"] == EffectState.AMBIGUOUS.value
    assert store._state["saga-ambiguous"]["status"] == "COMPENSATION_FAILED"
    assert store.list_dead_letters()[0]["step_name"] == "write"


def test_runtime_exception_is_recorded_as_ambiguous_and_quarantined() -> None:
    class FailingSandbox:
        def execute(self, _action: ActionPayload) -> dict[str, str]:
            raise TimeoutError("connection disappeared during request")

        def execute_compensation(self, _action: ActionPayload) -> bool:
            return True

    store = SagaStateStore()
    coordinator = SagaTransactionCoordinator(PassingVerifier(), FailingSandbox(), db_client=store)
    coordinator.start_transaction_log("saga-timeout", "ambiguous timeout", "tenant")

    assert coordinator.execute_saga("saga-timeout", [_step()]) is False

    assert store.get_effect("saga-timeout", "step-1")["state"] == EffectState.AMBIGUOUS.value
    assert store._state["saga-timeout"]["status"] == "COMPENSATION_FAILED"
    assert "outcome was durably recorded" in store.list_dead_letters()[0]["error"]


@pytest.mark.parametrize("boundary", ["recovery_before_compensation", "recovery_after_compensation"])
def test_recovery_itself_can_crash_and_resume_safely(boundary: str) -> None:
    store = SagaStateStore()
    sandbox = ObservableSandbox(applied={"s1"})
    store.write_transaction_state("saga-recovery", "RUNNING", {})
    store.prepare_effect("saga-recovery", "s1", "write", "CREATE", {"resource": "s1"}, "DELETE", {"resource": "s1"})
    store.transition_effect("saga-recovery", "s1", EffectState.PREPARED.value, EffectState.EXECUTING.value)
    store.transition_effect("saga-recovery", "s1", EffectState.EXECUTING.value, EffectState.APPLIED.value)

    crashing_recovery = SagaTransactionCoordinator(
        PassingVerifier(), sandbox, db_client=store, fault_injector=_crash_at(boundary)
    )
    with pytest.raises(InjectedCrash):
        crashing_recovery.recover()
    assert store.get_effect("saga-recovery", "s1")["state"] == EffectState.COMPENSATING.value

    restarted_again = SagaTransactionCoordinator(PassingVerifier(), sandbox, db_client=store)
    assert restarted_again.recover() == 1
    assert store.get_effect("saga-recovery", "s1")["state"] == EffectState.COMPENSATED.value
    assert sandbox.applied == set()
    assert sandbox.compensations == 1


def test_idempotent_receiver_can_safely_resolve_interrupted_execution() -> None:
    class IdempotentOnlySandbox(ObservableSandbox):
        resolve_effect = None  # type: ignore[assignment]

    store = SagaStateStore()
    sandbox = IdempotentOnlySandbox()
    store.write_transaction_state("saga-idem", "RUNNING", {})
    store.prepare_effect(
        "saga-idem", "s1", "write", "CREATE", {"resource": "s1"}, "DELETE", {"resource": "s1"}, "key-1"
    )
    store.transition_effect("saga-idem", "s1", EffectState.PREPARED.value, EffectState.EXECUTING.value)

    restarted = SagaTransactionCoordinator(PassingVerifier(), sandbox, db_client=store)
    assert restarted.recover() == 1
    assert sandbox.idempotent_executions == 1
    assert sandbox.compensations == 1
    assert store.get_effect("saga-idem", "s1")["state"] == EffectState.COMPENSATED.value


def test_prepare_is_idempotent_but_rejects_identity_reuse() -> None:
    store = SagaStateStore()
    first = store.prepare_effect("s", "x", "step", "CREATE", {"v": 1}, "DELETE", {"v": 1})
    second = store.prepare_effect("s", "x", "step", "CREATE", {"v": 1}, "DELETE", {"v": 1})
    assert first["seq"] == second["seq"]
    assert len(store.get_effects("s")) == 1

    with pytest.raises(JournalConflictError, match="reused with a different effect"):
        store.prepare_effect("s", "x", "step", "CREATE", {"v": 2}, "DELETE", {"v": 1})


def test_compare_and_set_rejects_stale_transition() -> None:
    store = SagaStateStore()
    store.prepare_effect("s", "x", "step", "CREATE", {}, "DELETE", {})
    store.transition_effect("s", "x", EffectState.PREPARED.value, EffectState.EXECUTING.value)

    with pytest.raises(JournalConflictError, match="expected"):
        store.transition_effect("s", "x", EffectState.PREPARED.value, EffectState.APPLIED.value)


def test_commit_atomically_publishes_idempotency_key() -> None:
    store = SagaStateStore()
    store.prepare_effect("s", "x", "step", "CREATE", {}, "DELETE", {}, "key")
    store.transition_effect("s", "x", EffectState.PREPARED.value, EffectState.EXECUTING.value)
    store.transition_effect("s", "x", EffectState.EXECUTING.value, EffectState.APPLIED.value)

    store.commit_effect("s", "x", "key")

    assert store.get_effect("s", "x")["state"] == EffectState.COMMITTED.value
    assert store.step_already_committed("s", "key") is True
