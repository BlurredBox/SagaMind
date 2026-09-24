"""
SagaMind MCP Server
====================

Exposes the SagaMind Core Runtime API as MCP tools so Claude Code (or any MCP client)
can start/drive sagas and query cognitive memory directly.

Config via environment variables:
    SAGAMIND_BASE_URL  — SagaMind API base URL (default: http://localhost:8000)
    SAGAMIND_API_KEY   — API key, if auth is enabled on the server (optional)

Run:
    python -m sdk.mcp_server

Claude Code config (~/.claude/mcp.json or project .mcp.json):
    {
      "mcpServers": {
        "sagamind": {
          "command": "python",
          "args": ["-m", "sdk.mcp_server"],
          "env": {"SAGAMIND_BASE_URL": "http://localhost:8000", "SAGAMIND_API_KEY": "..."}
        }
      }
    }
"""

from __future__ import annotations

import os
from typing import Any

from sdk.client import SagaMindClient

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - optional dependency
    raise RuntimeError(
        "The 'mcp' package is not installed. Install the 'mcp' extra "
        "(`pip install sagamind[mcp]`) to run the SagaMind MCP server."
    ) from exc

mcp = FastMCP("sagamind")

_client = SagaMindClient(
    base_url=os.environ.get("SAGAMIND_BASE_URL", "http://localhost:8000"),
    api_key=os.environ.get("SAGAMIND_API_KEY"),
)


@mcp.tool()
def saga_start(tenant_id: str, goal: str) -> dict[str, Any]:
    """Start a new saga transaction for *tenant_id* with the given *goal*. Returns saga_id."""
    return _client.start_saga(tenant_id, goal)


@mcp.tool()
def saga_step(
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
    """Submit and execute one saga step through the verification gate.

    Action tools are WRITE_FILE, DELETE_FILE, and NOOP. File rollback contracts are
    captured by the server; compensation tools are RESTORE_FILE, DELETE_FILE, and
    NOOP. *invariants* is an SMT-LIB2 assertion checked before execution. Set
    *requires_approval* to pause the saga for human approval (see saga_approve/reject).
    """
    return _client.submit_step(
        saga_id,
        step_name,
        tool_name,
        arguments,
        compensation_tool,
        compensation_arguments,
        invariants,
        idempotency_key,
        requires_approval,
    )


@mcp.tool()
def saga_status(saga_id: str) -> dict[str, Any]:
    """Get the current status and completed steps of a saga."""
    return _client.get_status(saga_id)


@mcp.tool()
def saga_approve(saga_id: str) -> dict[str, Any]:
    """Approve the step currently awaiting human approval and resume the saga."""
    return _client.approve(saga_id)


@mcp.tool()
def saga_reject(saga_id: str) -> dict[str, Any]:
    """Reject the step awaiting approval, rolling back any already-committed steps."""
    return _client.reject(saga_id)


@mcp.tool()
def saga_history(saga_id: str) -> dict[str, Any]:
    """Get the ordered forward-execution history of a saga (replay/time-travel debugging)."""
    return _client.history(saga_id)


@mcp.tool()
def saga_dead_letters() -> dict[str, Any]:
    """List sagas that reached COMPENSATION_FAILED and require manual operator review."""
    return _client.dead_letters()


@mcp.tool()
def memory_active(tenant_id: str, query: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Retrieve active (non-decayed) episodic memories for a tenant, optionally ranked by *query*.

    Each result includes related concepts from the semantic graph (GraphRAG).
    """
    return _client.active_memories(tenant_id, query=query, limit=limit)


@mcp.tool()
def memory_consolidate(tenant_id: str) -> dict[str, Any]:
    """Trigger an asynchronous memory consolidation sleep-cycle for a tenant."""
    return _client.consolidate(tenant_id)


@mcp.tool()
def sagamind_health() -> dict[str, Any]:
    """Check SagaMind service health and per-backend (live/fallback) status."""
    return _client.health()


if __name__ == "__main__":
    mcp.run()
