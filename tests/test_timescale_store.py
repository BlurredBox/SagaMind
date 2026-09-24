"""SagaMind — TimescaleMemoryStore active-memory retention tests (§4.2, in-memory fallback)."""

from datetime import datetime, timedelta, timezone

from src.memory.timescale_store import TimescaleMemoryStore


def _make_store_with_memories() -> TimescaleMemoryStore:
    store = TimescaleMemoryStore()
    assert store.pool_active is False
    now = datetime.now(timezone.utc)
    store.fallback_storage = [
        {
            "memory_id": "fresh",
            "tenant_id": "t1",
            "created_at": now,
            "last_retrieved_at": now,
            "agent_role": "agent-a",
            "summary": "recent memory",
            "importance_score": 0.8,
            "retrieval_count": 5,
            "embedding": [1.0, 0.0],
            "context_data": {},
        },
        {
            "memory_id": "stale",
            "tenant_id": "t1",
            "created_at": now - timedelta(days=365),
            "last_retrieved_at": now - timedelta(days=365),
            "agent_role": "agent-b",
            "summary": "ancient memory",
            "importance_score": 0.1,
            "retrieval_count": 0,
            "embedding": [0.0, 1.0],
            "context_data": {},
        },
    ]
    return store


class TestRetrieveActiveMemories:
    def test_fresh_memory_is_active(self):
        store = _make_store_with_memories()
        active = store.retrieve_active_memories("t1", [1.0, 0.0], s_init=12.0, gamma=0.45, tau=0.15)
        ids = {m["memory_id"] for m in active}
        assert "fresh" in ids
        assert "stale" not in ids

    def test_retention_field_present(self):
        store = _make_store_with_memories()
        active = store.retrieve_active_memories("t1", [1.0, 0.0], s_init=12.0, gamma=0.45, tau=0.15)
        fresh = next(m for m in active if m["memory_id"] == "fresh")
        assert fresh["retention"] > 0.99

    def test_tau_zero_returns_all(self):
        store = _make_store_with_memories()
        active = store.retrieve_active_memories("t1", [1.0, 0.0], s_init=12.0, gamma=0.45, tau=0.0)
        assert {m["memory_id"] for m in active} == {"fresh", "stale"}

    def test_retrieval_reinforces_selected_memory(self):
        store = _make_store_with_memories()
        previous = store.fallback_storage[0]["last_retrieved_at"]

        store.mark_retrieved(["fresh"])

        assert store.fallback_storage[0]["retrieval_count"] == 6
        assert store.fallback_storage[0]["last_retrieved_at"] >= previous
        assert store.fallback_storage[1]["retrieval_count"] == 0

    def test_list_tenants_is_unique_and_sorted(self):
        store = _make_store_with_memories()
        store.fallback_storage.append({**store.fallback_storage[0], "tenant_id": "a"})
        assert store.list_tenants() == ["a", "t1"]
