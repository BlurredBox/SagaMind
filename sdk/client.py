"""Thin synchronous HTTP client for the SagaMind Core Runtime API."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx


class SagaMindClient:
    """Minimal client wrapping the SagaMind HTTP API.

    Example:
        client = SagaMindClient("http://localhost:8000", api_key="secret-key")
        saga = client.start_saga(tenant_id="acme", goal="Provision tenant")
        client.submit_step(saga["saga_id"], step_name="write", tool_name="WRITE_FILE", ...)
    """

    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        headers = {"X-API-Key": api_key} if api_key else {}
        self._client = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SagaMindClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @staticmethod
    def _json_dict(response: httpx.Response) -> dict[str, Any]:
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError(f"Expected a JSON object, got {type(payload).__name__}.")
        return payload

    # ── Sagas ────────────────────────────────────────────────────────────
    def start_saga(self, tenant_id: str, goal: str) -> dict[str, Any]:
        r = self._client.post("/saga/start", json={"tenant_id": tenant_id, "goal": goal})
        r.raise_for_status()
        return self._json_dict(r)

    def submit_step(
        self,
        saga_id: str,
        step_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        compensation_tool: str,
        compensation_arguments: dict[str, Any],
        invariants: str,
        idempotency_key: str | None = None,
        requires_approval: bool = False,
    ) -> dict[str, Any]:
        r = self._client.post(
            "/saga/step",
            json={
                "saga_id": saga_id,
                "step_name": step_name,
                "tool_name": tool_name,
                "arguments": arguments,
                "compensation_tool": compensation_tool,
                "compensation_arguments": compensation_arguments,
                "invariants": invariants,
                "idempotency_key": idempotency_key,
                "requires_approval": requires_approval,
            },
        )
        r.raise_for_status()
        return self._json_dict(r)

    def get_status(self, saga_id: str) -> dict[str, Any]:
        r = self._client.get(f"/saga/{saga_id}/status")
        r.raise_for_status()
        return self._json_dict(r)

    def approve(self, saga_id: str) -> dict[str, Any]:
        r = self._client.post(f"/saga/{saga_id}/approve")
        r.raise_for_status()
        return self._json_dict(r)

    def reject(self, saga_id: str) -> dict[str, Any]:
        r = self._client.post(f"/saga/{saga_id}/reject")
        r.raise_for_status()
        return self._json_dict(r)

    def history(self, saga_id: str) -> dict[str, Any]:
        r = self._client.get(f"/saga/{saga_id}/history")
        r.raise_for_status()
        return self._json_dict(r)

    def stream(self, saga_id: str) -> Iterator[str]:
        """Yield raw SSE ``data: ...`` lines until the saga reaches a terminal state."""
        with self._client.stream("GET", f"/saga/{saga_id}/stream") as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if line:
                    yield line

    def dead_letters(self) -> dict[str, Any]:
        r = self._client.get("/saga/dead-letters")
        r.raise_for_status()
        return self._json_dict(r)

    # ── Memory ───────────────────────────────────────────────────────────
    def active_memories(
        self, tenant_id: str, query: str | None = None, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"tenant_id": tenant_id, "limit": limit, "offset": offset}
        if query:
            params["query"] = query
        r = self._client.get("/memory/active", params=params)
        r.raise_for_status()
        return self._json_dict(r)

    def ingest_memory(
        self,
        tenant_id: str,
        agent_role: str,
        summary: str,
        importance: float,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        r = self._client.post(
            "/memory",
            json={
                "tenant_id": tenant_id,
                "agent_role": agent_role,
                "summary": summary,
                "importance": importance,
                "context": context or {},
            },
        )
        r.raise_for_status()
        return self._json_dict(r)

    def consolidate(self, tenant_id: str) -> dict[str, Any]:
        r = self._client.post("/memory/consolidate", params={"tenant_id": tenant_id})
        r.raise_for_status()
        return self._json_dict(r)

    # ── Speculative execution ───────────────────────────────────────────
    def run_speculative(
        self,
        tenant_id: str,
        drafts: list[dict[str, Any]],
        goal: str = "speculative action",
    ) -> dict[str, Any]:
        r = self._client.post(
            "/speculative/run",
            json={"tenant_id": tenant_id, "goal": goal, "drafts": drafts},
        )
        r.raise_for_status()
        return self._json_dict(r)

    # ── Health ───────────────────────────────────────────────────────────
    def health(self) -> dict[str, Any]:
        r = self._client.get("/health")
        r.raise_for_status()
        return self._json_dict(r)

    def readiness(self) -> dict[str, Any]:
        r = self._client.get("/ready")
        r.raise_for_status()
        return self._json_dict(r)
