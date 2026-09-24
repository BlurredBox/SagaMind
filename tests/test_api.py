"""
SagaMind — FastAPI Integration Tests
======================================

Tests the HTTP API endpoints using FastAPI's TestClient.
All store initializations in src.main are patched to prevent
real database connections during testing.
"""

from unittest.mock import MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────
# Patch all external store constructors BEFORE importing the app.
# This prevents TimescaleDB, Neo4j, and wasmtime connections at import time.
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    """
    Create a TestClient with all external dependencies mocked out.
    Patches are applied before importing src.main so module-level
    singleton initialisations don't attempt real connections.
    """
    with (
        patch("src.main.TimescaleMemoryStore") as mock_ts_cls,
        patch("src.main.Neo4jGraphStore") as mock_neo_cls,
        patch("src.main.WasmSandbox") as mock_sb_cls,
        patch("src.main.Z3Verifier") as mock_ver_cls,
        patch("src.main.EbbinghausMemoryManager") as mock_decay_cls,
        patch("src.main.MemoryConsolidator") as mock_cons_cls,
    ):
        # Configure mock return values so the coordinator works
        mock_ver = MagicMock()
        mock_ver.verify = MagicMock(return_value=(True, "OK"))
        mock_ver_cls.return_value = mock_ver

        mock_sb = MagicMock()
        mock_sb.execute = MagicMock(return_value={"status": "SUCCESS"})
        mock_sb.execute_compensation = MagicMock(return_value=True)
        mock_sb_cls.return_value = mock_sb

        mock_ts_cls.return_value = MagicMock()
        mock_neo_cls.return_value = MagicMock()
        mock_decay_cls.return_value = MagicMock()
        mock_cons_cls.return_value = MagicMock()

        # Force reload of the module so singletons pick up mocks
        import importlib

        import src.main

        importlib.reload(src.main)

        from fastapi.testclient import TestClient

        yield TestClient(src.main.app)


# ─────────────────────────────────────────────────────────────────────
# Health Endpoint
# ─────────────────────────────────────────────────────────────────────


class TestHealthEndpoint:
    """Validate the /health readiness probe."""

    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "HEALTHY"
        assert "environment" in body

    def test_health_returns_environment(self, client):
        resp = client.get("/health")
        body = resp.json()
        # Default env from config is "development"
        assert isinstance(body["environment"], str)


# ─────────────────────────────────────────────────────────────────────
# Start Saga Endpoint
# ─────────────────────────────────────────────────────────────────────


class TestStartSagaEndpoint:
    """Validate POST /saga/start creates a new saga."""

    def test_start_saga_endpoint(self, client):
        resp = client.post(
            "/saga/start",
            json={"tenant_id": "tenant-test", "goal": "integration test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "saga_id" in body
        assert body["status"] == "RUNNING"
        # saga_id should look like a UUID
        assert len(body["saga_id"]) == 36

    def test_start_saga_missing_fields(self, client):
        """Missing required fields should return 422."""
        resp = client.post("/saga/start", json={})
        assert resp.status_code == 422

    def test_start_saga_missing_tenant_id(self, client):
        resp = client.post("/saga/start", json={"goal": "deploy"})
        assert resp.status_code == 422

    def test_start_saga_missing_goal(self, client):
        resp = client.post("/saga/start", json={"tenant_id": "t1"})
        assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────────────
# 404 on Unknown Routes
# ─────────────────────────────────────────────────────────────────────


class TestApprovalWorkflow:
    """Validate /saga/{id}/approve, /reject and /history (§6.5, §6.6)."""

    def test_step_requires_approval_returns_pending(self, client):
        saga_id = client.post("/saga/start", json={"tenant_id": "tenant-1", "goal": "g"}).json()["saga_id"]
        resp = client.post(
            "/saga/step",
            json={
                "saga_id": saga_id,
                "step_name": "risky",
                "tool_name": "NOOP",
                "arguments": {},
                "compensation_tool": "NOOP",
                "compensation_arguments": {},
                "invariants": "(assert true)",
                "requires_approval": True,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "AWAITING_APPROVAL"

        status_resp = client.get(f"/saga/{saga_id}/status")
        assert status_resp.json()["status"] == "AWAITING_APPROVAL"

        approve_resp = client.post(f"/saga/{saga_id}/approve")
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "COMMITTED"

        history_resp = client.get(f"/saga/{saga_id}/history")
        assert history_resp.status_code == 200

    def test_approve_unknown_saga_404(self, client):
        resp = client.post("/saga/00000000-0000-0000-0000-000000000000/approve")
        assert resp.status_code == 404

    def test_approve_when_not_awaiting_returns_409(self, client):
        saga_id = client.post("/saga/start", json={"tenant_id": "tenant-1", "goal": "g"}).json()["saga_id"]
        resp = client.post(f"/saga/{saga_id}/approve")
        assert resp.status_code == 409

    def test_reject_rolls_back(self, client):
        saga_id = client.post("/saga/start", json={"tenant_id": "tenant-1", "goal": "g"}).json()["saga_id"]
        client.post(
            "/saga/step",
            json={
                "saga_id": saga_id,
                "step_name": "risky",
                "tool_name": "NOOP",
                "arguments": {},
                "compensation_tool": "NOOP",
                "compensation_arguments": {},
                "invariants": "(assert true)",
                "requires_approval": True,
            },
        )
        reject_resp = client.post(f"/saga/{saga_id}/reject")
        assert reject_resp.status_code == 200
        assert reject_resp.json()["status"] == "ROLLED_BACK"


class TestUnknownRoutes:
    """Verify that non-existent routes return proper errors."""

    def test_unknown_get(self, client):
        resp = client.get("/nonexistent")
        assert resp.status_code == 404

    def test_unknown_post(self, client):
        resp = client.post("/nonexistent", json={})
        assert resp.status_code in (404, 405)


class TestMemoryIngestion:
    def test_memory_can_be_ingested(self, client):
        response = client.post(
            "/memory",
            json={
                "tenant_id": "tenant-test",
                "agent_role": "planner",
                "summary": "Selected a safe deployment plan.",
                "importance": 0.8,
                "context": {"source": "test"},
            },
        )
        assert response.status_code == 201
        assert response.json()["status"] == "CREATED"
        assert len(response.json()["memory_id"]) == 36

    def test_memory_rejects_invalid_importance(self, client):
        response = client.post(
            "/memory",
            json={
                "tenant_id": "tenant-test",
                "agent_role": "planner",
                "summary": "bad",
                "importance": 2,
            },
        )
        assert response.status_code == 422
