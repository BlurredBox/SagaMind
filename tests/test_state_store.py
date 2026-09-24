"""
SagaMind — Saga State Store Tests (in-memory backend)
=====================================================

Exercises the durable-store interface in its offline fallback mode (no Postgres/Redis),
which is the contract the coordinator depends on.
"""

from src.orchestrator.state_store import SagaStateStore


class TestBackendSelection:
    def test_defaults_to_memory_offline(self):
        store = SagaStateStore()
        assert store.backend == "memory"


class TestStateLifecycle:
    def test_running_saga_is_incomplete(self):
        store = SagaStateStore()
        store.write_transaction_state("s1", "RUNNING", {"goal": "g", "tenant_id": "t1"})
        ids = {s["saga_id"] for s in store.list_incomplete()}
        assert "s1" in ids

    def test_committed_saga_is_not_incomplete(self):
        store = SagaStateStore()
        store.write_transaction_state("s1", "RUNNING", {})
        store.write_transaction_state("s1", "COMMITTED", {})
        ids = {s["saga_id"] for s in store.list_incomplete()}
        assert "s1" not in ids

    def test_rolled_back_is_terminal(self):
        store = SagaStateStore()
        store.write_transaction_state("s2", "RUNNING", {})
        store.write_transaction_state("s2", "ROLLED_BACK", {})
        assert all(s["saga_id"] != "s2" for s in store.list_incomplete())


class TestStepHistory:
    def test_record_and_get_history(self):
        store = SagaStateStore()
        store.record_step("s4", "step-1", "WRITE_FILE", {"path": "/a"}, {"status": "SUCCESS"}, "COMMITTED")
        store.record_step("s4", "step-2", "NOOP", {}, {}, "COMMITTED")
        history = store.get_history("s4")
        assert [h["step_name"] for h in history] == ["step-1", "step-2"]
        assert history[0]["tool_name"] == "WRITE_FILE"
        assert history[0]["status"] == "COMMITTED"

    def test_history_empty_for_unknown_saga(self):
        store = SagaStateStore()
        assert store.get_history("nonexistent") == []


class TestCompensationLog:
    def test_compensations_recorded_in_order(self):
        store = SagaStateStore()
        store.write_transaction_state("s3", "RUNNING", {})
        store.append_compensation("s3", "DELETE_FILE", {"path": "/a"})
        store.append_compensation("s3", "RESTORE_FILE", {"path": "/a", "existed": False, "previous": ""})
        incomplete = {s["saga_id"]: s for s in store.list_incomplete()}
        comps = incomplete["s3"]["compensations"]
        assert [c["tool_name"] for c in comps] == ["DELETE_FILE", "RESTORE_FILE"]


class TestDeadLetters:
    def test_tenant_filter_prevents_cross_tenant_visibility(self):
        store = SagaStateStore()
        store.write_transaction_state("s-a", "RUNNING", {"tenant_id": "a"})
        store.write_transaction_state("s-b", "RUNNING", {"tenant_id": "b"})
        store.push_dead_letter("s-a", "one", "failed")
        store.push_dead_letter("s-b", "two", "failed")

        assert [item["saga_id"] for item in store.list_dead_letters("a")] == ["s-a"]
        assert {item["saga_id"] for item in store.list_dead_letters()} == {"s-a", "s-b"}
