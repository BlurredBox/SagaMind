"""
SagaMind — Crash Recovery Tests
===============================

Validates that the coordinator replays persisted compensations (LIFO) for sagas left
incomplete by a previous process, using the in-memory durable store.
"""

from unittest.mock import MagicMock

from src.orchestrator.coordinator import SagaTransactionCoordinator
from src.orchestrator.state_store import SagaStateStore


class TestRecover:
    def test_awaiting_approval_survives_coordinator_restart(self):
        from src.models import ActionPayload, SagaStatus, SagaStep

        store = SagaStateStore()
        verifier = MagicMock()
        verifier.verify.return_value = (True, "OK")
        first_sandbox = MagicMock()
        first = SagaTransactionCoordinator(verifier, first_sandbox, db_client=store)
        first.start_transaction_log("approval-restart", "deploy", "tenant-a")
        step = SagaStep(
            step_id="approval-step",
            step_name="approve deploy",
            action=ActionPayload("NOOP", {}),
            compensation=ActionPayload("NOOP", {}),
            invariants="(assert true)",
            requires_approval=True,
        )
        assert first.execute_saga("approval-restart", [step]) is False

        second_sandbox = MagicMock()
        second_sandbox.execute.return_value = {"status": "SUCCESS"}
        second = SagaTransactionCoordinator(verifier, second_sandbox, db_client=store)
        assert second.recover() == 0
        restored = second.active_sagas["approval-restart"]
        assert restored.status == SagaStatus.AWAITING_APPROVAL.value
        assert restored.tenant_id == "tenant-a"
        assert restored.pending_steps[0].step_id == "approval-step"

        assert second.resume_after_approval("approval-restart") is True
        assert second.active_sagas["approval-restart"].status == SagaStatus.COMMITTED.value

    def test_recover_replays_compensations_lifo(self):
        store = SagaStateStore()
        store.write_transaction_state("s1", "RUNNING", {})
        store.append_compensation("s1", "DELETE_FILE", {"path": "/a"})
        store.append_compensation("s1", "RESTORE_FILE", {"path": "/b", "existed": False, "previous": ""})

        sandbox = MagicMock()
        sandbox.execute_compensation = MagicMock(return_value=True)
        coord = SagaTransactionCoordinator(MagicMock(), sandbox, db_client=store)

        recovered = coord.recover()

        assert recovered == 1
        assert sandbox.execute_compensation.call_count == 2
        order = [c.args[0].tool_name for c in sandbox.execute_compensation.call_args_list]
        assert order == ["RESTORE_FILE", "DELETE_FILE"]  # reverse of append order
        # The saga is now terminal and no longer eligible for recovery.
        assert all(s["saga_id"] != "s1" for s in store.list_incomplete())

    def test_recover_noop_without_db(self):
        coord = SagaTransactionCoordinator(MagicMock(), MagicMock())
        assert coord.recover() == 0

    def test_recover_does_not_claim_success_when_compensation_fails(self):
        store = SagaStateStore()
        store.write_transaction_state("s-fail", "RUNNING", {})
        store.append_compensation("s-fail", "DELETE_FILE", {"path": "/a"})
        sandbox = MagicMock()
        sandbox.execute_compensation = MagicMock(return_value=False)
        coord = SagaTransactionCoordinator(MagicMock(), sandbox, db_client=store)

        assert coord.recover() == 0
        assert store._state["s-fail"]["status"] == "COMPENSATION_FAILED"
        assert store.list_dead_letters()[0]["saga_id"] == "s-fail"

    def test_execute_saga_persists_state_when_db_wired(self):
        store = SagaStateStore()
        verifier = MagicMock()
        verifier.verify = MagicMock(return_value=(True, "OK"))
        sandbox = MagicMock()
        sandbox.execute = MagicMock(return_value={"status": "SUCCESS"})

        from src.models import ActionPayload, SagaStep

        coord = SagaTransactionCoordinator(verifier, sandbox, db_client=store)
        coord.start_transaction_log("saga-d", "persist test", "t1")
        step = SagaStep(
            step_id="x",
            step_name="write",
            action=ActionPayload("WRITE_FILE", {"path": "/p"}),
            compensation=ActionPayload("DELETE_FILE", {"path": "/p"}),
            invariants="",
        )
        assert coord.execute_saga("saga-d", [step]) is True
        # Durable store reflects the terminal COMMITTED state.
        assert all(s["saga_id"] != "saga-d" for s in store.list_incomplete())
