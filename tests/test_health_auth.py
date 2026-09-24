"""
SagaMind — Health & Authentication Tests
========================================

Validates the public readiness probe and API-key enforcement. The app is reloaded with
its real (gracefully-degrading) singletons so no external services are required.
"""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_module():
    import src.main as main

    importlib.reload(main)  # ensure real singletons (undo any prior patched reload)
    return main


@pytest.fixture
def client(app_module):
    return TestClient(app_module.app)


class TestHealth:
    def test_health_is_public_and_reports_backends(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "HEALTHY"
        assert set(body["backends"]) >= {"timescale", "neo4j", "verifier", "wasm"}

    def test_metrics_endpoint_is_public(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_readiness_reports_truthful_isolation_modes(self, client):
        resp = client.get("/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "READY"
        assert body["backends"]["isolation"] == "isolated-worker"
        assert body["backends"]["wasi"] in {"live", "unavailable"}


class TestAuthDisabledByDefault:
    def test_saga_start_open_when_no_keys(self, client):
        resp = client.post("/saga/start", json={"tenant_id": "t", "goal": "g"})
        assert resp.status_code == 200

    def test_invalid_content_length_is_rejected(self, client):
        resp = client.post(
            "/saga/start",
            content='{"tenant_id":"t","goal":"g"}',
            headers={"Content-Length": "not-a-number", "Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    def test_declared_oversized_body_is_rejected(self, client, app_module, monkeypatch):
        monkeypatch.setattr(app_module.settings, "max_request_bytes", 5)
        resp = client.post("/saga/start", json={"tenant_id": "t", "goal": "g"})
        assert resp.status_code == 413


class TestAuthEnforced:
    @pytest.fixture(autouse=True)
    def _enable_auth(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module.settings, "api_keys", "secret-key")

    def test_missing_key_rejected(self, client):
        resp = client.post("/saga/start", json={"tenant_id": "t", "goal": "g"})
        assert resp.status_code == 401

    def test_wrong_key_rejected(self, client):
        resp = client.post(
            "/saga/start",
            json={"tenant_id": "t", "goal": "g"},
            headers={"X-API-Key": "nope"},
        )
        assert resp.status_code == 401

    def test_valid_key_accepted(self, client):
        resp = client.post(
            "/saga/start",
            json={"tenant_id": "t", "goal": "g"},
            headers={"X-API-Key": "secret-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "RUNNING"

    def test_health_remains_public_under_auth(self, client):
        assert client.get("/health").status_code == 200
