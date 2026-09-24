"""Isolated Temporal workflow implementing its documented Saga pattern."""

from __future__ import annotations

import contextlib
from datetime import timedelta
from pathlib import Path
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy


@activity.defn(name="external_validation_apply")
async def temporal_apply(operation: dict[str, Any]) -> None:
    if operation["op"] == "fail":
        raise RuntimeError("injected workload failure")
    path = Path(str(operation["path"]))
    if bool(operation.get("guard_path")):
        workspace = Path(str(operation["workspace"]))
        try:
            path.relative_to(workspace)
        except ValueError as exc:
            raise RuntimeError("path policy rejected") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(operation["content"]), encoding="utf-8")


@activity.defn(name="external_validation_compensate")
async def temporal_compensate(operation: dict[str, Any]) -> None:
    path = Path(str(operation["path"]))
    if bool(operation["existed"]):
        path.write_text(str(operation["previous"]), encoding="utf-8")
    elif path.exists():
        path.unlink()


@workflow.defn(name="ExternalValidationSaga")
class TemporalSagaWorkflow:
    @workflow.run
    async def run(self, operations: list[dict[str, Any]]) -> bool:
        compensations: list[dict[str, Any]] = []
        try:
            for operation in operations:
                # Temporal's guidance registers compensation before the effect.
                compensations.append(operation)
                await workflow.execute_activity(
                    temporal_apply,
                    operation,
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
            return True
        except Exception:
            for operation in reversed(compensations):
                with contextlib.suppress(Exception):
                    await workflow.execute_activity(
                        temporal_compensate,
                        operation,
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=RetryPolicy(maximum_attempts=1),
                    )
            return False
