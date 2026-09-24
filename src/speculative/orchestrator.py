"""
SagaMind Speculative Orchestrator
=================================

Runs several candidate ("draft") actions concurrently, validates each in isolation, and
commits only the first that passes — overlapping the validation latency of independent
drafts instead of paying it sequentially.

Speculation is intentionally limited to side-effect-free validation. The API hands the
winner to the normal Saga coordinator, so durable journaling, compensation, approval,
and recovery semantics are identical to a non-speculative step.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from src.models import ActionPayload
from src.orchestrator.sandbox import SandboxError

logger = logging.getLogger("SagaMind.Speculative")


class SpeculativeOrchestrator:
    """Validate multiple draft actions in parallel; commit the first valid one."""

    def __init__(self, sandbox: Any):
        self.sandbox = sandbox
        self.active_sandboxes: dict[str, dict[str, Any]] = {}

    async def run_speculative_drafts(self, drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Validate all *drafts* concurrently and return their per-draft results."""
        tasks = []
        for draft in drafts:
            sandbox_id = f"sb-{uuid.uuid4().hex[:6]}"
            logger.info(
                "[Speculative] Validating draft '%s' in sandbox '%s'.",
                draft.get("command"),
                sandbox_id,
            )
            tasks.append(self.execute_draft_async(sandbox_id, draft))
        return await asyncio.gather(*tasks)

    async def execute_draft_async(self, sandbox_id: str, draft: dict[str, Any]) -> dict[str, Any]:
        """Validate a single draft without materialising side effects."""
        tool = draft.get("command")
        args = draft.get("arguments", {})
        action = ActionPayload(tool_name=str(tool), arguments=dict(args))

        # Validation runs off the event loop to model concurrent isolated checks.
        valid, error = await asyncio.to_thread(self._validate, action)

        self.active_sandboxes[sandbox_id] = {
            "sandbox_id": sandbox_id,
            "action": action,
            "valid": valid,
            "committed": False,
        }

        result: dict[str, Any] = {"sandbox_id": sandbox_id, "command": tool, "success": valid}
        if valid:
            result["state_diff_hash"] = uuid.uuid4().hex[:8]
        else:
            result["error"] = error
        return result

    def _validate(self, action: ActionPayload) -> tuple[bool, str]:
        try:
            self.sandbox.validate(action)
            return True, ""
        except (SandboxError, ValueError, TypeError) as exc:
            return False, str(exc)

    def select_winner(self, results: list[dict[str, Any]]) -> tuple[str, ActionPayload] | None:
        """Select the first valid draft without materialising an external effect."""
        for result in results:
            sandbox_id = str(result["sandbox_id"])
            meta = self.active_sandboxes.get(sandbox_id)
            if result.get("success") and meta is not None and meta["valid"]:
                self._discard_others(sandbox_id)
                return sandbox_id, meta["action"]
        return None

    def _discard_others(self, keep_id: str) -> None:
        for sid, meta in self.active_sandboxes.items():
            if sid != keep_id:
                meta["valid"] = False
