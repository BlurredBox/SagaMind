"""
SagaMind Saga Transaction Coordinator
======================================

Stateful saga transaction engine enforcing eventual consistency across
non-deterministic multi-agent execution paths.

Architecture:
    - Forward execution: steps are committed sequentially through verification gates.
    - Rollback: compensations are executed in LIFO (reverse chronological) order.
    - Each step passes through a Z3 verification gate before sandbox execution.
    - Idempotency: duplicate step submissions (same idempotency_key) are detected and
      short-circuited before re-execution.
    - Dead-letter: COMPENSATION_FAILED sagas are pushed to the dead-letter queue for
      operator review instead of being silently abandoned.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

from src.models import ActionPayload, SagaStatus, SagaStep, SagaTransaction, StepStatus
from src.observability import metrics, span
from src.orchestrator.state_store import EffectState

logger = logging.getLogger("SagaMind.Orchestrator")


class CoordinatorError(Exception):
    """Coordinator-level transaction failures."""


class InjectedCrash(BaseException):
    """Hard-crash sentinel for deterministic boundary fault injection.

    It derives from ``BaseException`` so the coordinator's normal failure handler does
    not turn a simulated process death into an orderly rollback.
    """


class SagaTransactionCoordinator:
    """
    Stateful Saga transaction engine enforcing consistency across
    non-deterministic multi-agent paths.
    """

    def __init__(
        self,
        verifier_instance: Any,
        sandbox_instance: Any,
        db_client: Any | None = None,
        max_active_sagas: int = 10_000,
        fault_injector: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self.verifier = verifier_instance
        self.sandbox = sandbox_instance
        self.db = db_client
        self.max_active_sagas = max_active_sagas
        self.fault_injector = fault_injector
        self.active_sagas: dict[str, SagaTransaction] = {}

    def _checkpoint(self, name: str, **context: Any) -> None:
        """Expose every durability boundary to deterministic crash tests."""
        if self.fault_injector is not None:
            self.fault_injector(name, context)

    # ── Status ───────────────────────────────────────────────────────────
    def get_saga_status(self, saga_id: str) -> dict[str, Any] | None:
        """Return a snapshot of the saga's current state, or None if unknown."""
        saga = self.active_sagas.get(saga_id)
        if saga is None:
            return None
        return {
            "saga_id": saga.saga_id,
            "tenant_id": saga.tenant_id,
            "goal": saga.goal,
            "status": saga.status,
            "start_time": saga.start_time,
            "completed_steps": [s.step_name for s in saga.completed_steps],
        }

    # ── Recovery ─────────────────────────────────────────────────────────
    def recover(self) -> int:
        """Replay persisted compensations for sagas left incomplete by a crash.

        Returns the number of sagas rolled back.
        """
        if not self.db or not hasattr(self.db, "list_incomplete"):
            return 0
        recovered = 0
        for saga in self.db.list_incomplete():
            saga_id = saga["saga_id"]
            effects = saga.get("effects", [])
            metadata = saga.get("metadata") or {}
            if saga.get("status") == SagaStatus.AWAITING_APPROVAL.value and metadata.get("pending_steps"):
                completed = [self._step_from_effect(effect) for effect in effects if effect["state"] == "COMMITTED"]
                pending = [self._deserialize_step(item) for item in metadata["pending_steps"]]
                self.active_sagas[saga_id] = SagaTransaction(
                    saga_id=saga_id,
                    tenant_id=saga.get("tenant_id") or metadata.get("tenant_id") or "",
                    goal=saga.get("goal") or metadata.get("goal") or "",
                    status=SagaStatus.AWAITING_APPROVAL.value,
                    start_time=float(metadata.get("start_time", time.time())),
                    completed_steps=completed,
                    pending_steps=pending,
                )
                logger.warning("[RECOVERY] Restored saga %s awaiting approval.", saga_id)
                continue
            if effects:
                if self._recover_effects(saga_id, effects):
                    self.db.write_transaction_state(saga_id, SagaStatus.ROLLED_BACK.value, {"recovered": True})
                    recovered += 1
                continue
            comps = saga.get("compensations", [])
            logger.warning("[RECOVERY] Compensating %d step(s) for incomplete saga %s", len(comps), saga_id)
            recovery_error: str | None = None
            for comp in reversed(comps):
                try:
                    ok = self.sandbox.execute_compensation(ActionPayload(comp["tool_name"], comp.get("arguments", {})))
                    if not ok:
                        recovery_error = f"Compensation '{comp['tool_name']}' returned False"
                        break
                except Exception as exc:  # noqa: BLE001 - best-effort recovery
                    logger.error("[RECOVERY] Compensation failed for saga %s: %s", saga_id, exc)
                    recovery_error = str(exc)
                    break
            if recovery_error is not None:
                self.db.write_transaction_state(
                    saga_id,
                    SagaStatus.COMPENSATION_FAILED.value,
                    {"recovered": False, "error": recovery_error},
                )
                if hasattr(self.db, "push_dead_letter"):
                    self.db.push_dead_letter(saga_id, "crash_recovery", recovery_error)
                continue
            self.db.write_transaction_state(saga_id, SagaStatus.ROLLED_BACK.value, {"recovered": True})
            recovered += 1
        if recovered:
            logger.warning("[RECOVERY] Rolled back %d incomplete saga(s) on startup.", recovered)
        return recovered

    @staticmethod
    def _serialize_step(step: SagaStep) -> dict[str, Any]:
        return {
            "step_id": step.step_id,
            "step_name": step.step_name,
            "action": {"tool_name": step.action.tool_name, "arguments": step.action.arguments},
            "compensation": {
                "tool_name": step.compensation.tool_name,
                "arguments": step.compensation.arguments,
            },
            "invariants": step.invariants,
            "status": step.status,
            "error": step.error,
            "idempotency_key": step.idempotency_key,
            "requires_approval": step.requires_approval,
            "approved": step.approved,
        }

    @staticmethod
    def _deserialize_step(value: dict[str, Any]) -> SagaStep:
        return SagaStep(
            step_id=value["step_id"],
            step_name=value["step_name"],
            action=ActionPayload(value["action"]["tool_name"], dict(value["action"].get("arguments", {}))),
            compensation=ActionPayload(
                value["compensation"]["tool_name"],
                dict(value["compensation"].get("arguments", {})),
            ),
            invariants=value.get("invariants", ""),
            status=value.get("status", StepStatus.PENDING.value),
            error=value.get("error", ""),
            idempotency_key=value.get("idempotency_key"),
            requires_approval=bool(value.get("requires_approval")),
            approved=bool(value.get("approved")),
        )

    @classmethod
    def _step_from_effect(cls, effect: dict[str, Any]) -> SagaStep:
        return SagaStep(
            step_id=effect["step_id"],
            step_name=effect["step_name"],
            action=ActionPayload(effect["action"]["tool_name"], dict(effect["action"].get("arguments", {}))),
            compensation=ActionPayload(
                effect["compensation"]["tool_name"],
                dict(effect["compensation"].get("arguments", {})),
            ),
            invariants="",
            status=StepStatus.COMMITTED.value,
            idempotency_key=effect.get("idempotency_key"),
        )

    def _recover_effects(self, saga_id: str, effects: list[dict[str, Any]]) -> bool:
        """Resolve a write-ahead journal without assuming ambiguous calls failed."""
        assert self.db is not None
        logger.warning("[RECOVERY] Resolving %d journaled effect(s) for saga %s", len(effects), saga_id)
        for effect in reversed(effects):
            state = effect["state"]
            if state in {EffectState.ABORTED.value, EffectState.COMPENSATED.value}:
                continue
            if state == EffectState.COMPENSATION_FAILED.value:
                self._dead_letter_recovery(saga_id, effect, effect.get("error") or "prior compensation failure")
                return False
            if state == EffectState.PREPARED.value:
                self.db.transition_effect(
                    saga_id,
                    effect["step_id"],
                    EffectState.PREPARED.value,
                    EffectState.ABORTED.value,
                )
                continue
            if state in {EffectState.EXECUTING.value, EffectState.AMBIGUOUS.value}:
                try:
                    resolution = self._resolve_forward_effect(effect)
                except Exception as exc:  # noqa: BLE001 - resolver failure is an ambiguous outcome
                    self._mark_ambiguous(saga_id, effect, f"effect resolver failed: {exc}")
                    return False
                if resolution == "NOT_APPLIED":
                    self.db.transition_effect(
                        saga_id,
                        effect["step_id"],
                        {EffectState.EXECUTING.value, EffectState.AMBIGUOUS.value},
                        EffectState.ABORTED.value,
                    )
                    continue
                if resolution == "APPLIED":
                    effect = self.db.transition_effect(
                        saga_id,
                        effect["step_id"],
                        {EffectState.EXECUTING.value, EffectState.AMBIGUOUS.value},
                        EffectState.APPLIED.value,
                    )
                else:
                    self._mark_ambiguous(saga_id, effect, "forward effect outcome could not be determined")
                    return False
            if effect["state"] == EffectState.COMPENSATING.value:
                try:
                    resolution = self._resolve_compensation(effect)
                except Exception as exc:  # noqa: BLE001 - quarantine an unresolvable compensation
                    self._fail_recovery_compensation(saga_id, effect, f"compensation resolver failed: {exc}")
                    return False
                if resolution == "APPLIED":
                    self.db.transition_effect(
                        saga_id,
                        effect["step_id"],
                        EffectState.COMPENSATING.value,
                        EffectState.COMPENSATED.value,
                    )
                    continue
                if resolution != "NOT_APPLIED":
                    self._fail_recovery_compensation(saga_id, effect, "compensation outcome could not be determined")
                    return False
                if not self._recover_compensation(saga_id, effect, already_marked=True):
                    return False
                continue
            if effect["state"] in {
                EffectState.APPLIED.value,
                EffectState.COMMITTED.value,
            } and not self._recover_compensation(saga_id, effect):
                return False
        return True

    def _resolve_forward_effect(self, effect: dict[str, Any]) -> str:
        """Return APPLIED, NOT_APPLIED, or UNKNOWN for an interrupted call."""
        key = effect.get("idempotency_key")
        if key and self.db is not None and self.db.step_already_committed(effect["saga_id"], key):
            return "APPLIED"
        resolver = getattr(self.sandbox, "resolve_effect", None)
        if callable(resolver):
            return self._normalise_resolution(resolver(effect))
        execute_idempotent = getattr(self.sandbox, "execute_idempotent", None)
        if key and callable(execute_idempotent):
            action = ActionPayload(effect["action"]["tool_name"], effect["action"].get("arguments", {}))
            result = execute_idempotent(action, key)
            if result is not None and getattr(result, "success", True) is True:
                return "APPLIED"
        return "UNKNOWN"

    def _resolve_compensation(self, effect: dict[str, Any]) -> str:
        resolver = getattr(self.sandbox, "resolve_compensation", None)
        if callable(resolver):
            return self._normalise_resolution(resolver(effect))
        return "UNKNOWN"

    @staticmethod
    def _normalise_resolution(value: Any) -> str:
        if value is True:
            return "APPLIED"
        if value is False:
            return "NOT_APPLIED"
        normalized = str(value).upper()
        if normalized in {"APPLIED", "NOT_APPLIED"}:
            return normalized
        return "UNKNOWN"

    def _recover_compensation(
        self,
        saga_id: str,
        effect: dict[str, Any],
        *,
        already_marked: bool = False,
    ) -> bool:
        assert self.db is not None
        step_id = effect["step_id"]
        if not already_marked:
            effect = self.db.transition_effect(
                saga_id,
                step_id,
                {EffectState.APPLIED.value, EffectState.COMMITTED.value},
                EffectState.COMPENSATING.value,
            )
        self._checkpoint("recovery_before_compensation", saga_id=saga_id, step_id=step_id)
        try:
            comp = effect["compensation"]
            ok = self.sandbox.execute_compensation(ActionPayload(comp["tool_name"], comp.get("arguments", {})))
            if not ok:
                self._fail_recovery_compensation(saga_id, effect, "compensation returned False")
                return False
            self._checkpoint("recovery_after_compensation", saga_id=saga_id, step_id=step_id)
            self.db.transition_effect(
                saga_id,
                step_id,
                EffectState.COMPENSATING.value,
                EffectState.COMPENSATED.value,
            )
            return True
        except Exception as exc:  # noqa: BLE001 - recovery must quarantine failures
            self._fail_recovery_compensation(saga_id, effect, str(exc))
            return False

    def _mark_ambiguous(self, saga_id: str, effect: dict[str, Any], error: str) -> None:
        assert self.db is not None
        if effect["state"] == EffectState.EXECUTING.value:
            self.db.transition_effect(
                saga_id,
                effect["step_id"],
                EffectState.EXECUTING.value,
                EffectState.AMBIGUOUS.value,
                error=error,
            )
        self._dead_letter_recovery(saga_id, effect, error)

    def _fail_recovery_compensation(self, saga_id: str, effect: dict[str, Any], error: str) -> None:
        assert self.db is not None
        self.db.transition_effect(
            saga_id,
            effect["step_id"],
            EffectState.COMPENSATING.value,
            EffectState.COMPENSATION_FAILED.value,
            error=error,
        )
        self._dead_letter_recovery(saga_id, effect, error)

    def _dead_letter_recovery(self, saga_id: str, effect: dict[str, Any], error: str) -> None:
        assert self.db is not None
        self.db.write_transaction_state(
            saga_id,
            SagaStatus.COMPENSATION_FAILED.value,
            {"recovered": False, "error": error, "failed_step": effect.get("step_name")},
        )
        if hasattr(self.db, "push_dead_letter"):
            self.db.push_dead_letter(saga_id, effect.get("step_name", "crash_recovery"), error)

    # ── Eviction ─────────────────────────────────────────────────────────
    def _evict_if_needed(self) -> None:
        """Bound in-memory saga retention to prevent unbounded growth."""
        if len(self.active_sagas) <= self.max_active_sagas:
            return
        terminal = {
            SagaStatus.COMMITTED.value,
            SagaStatus.ROLLED_BACK.value,
            SagaStatus.COMPENSATION_FAILED.value,
        }
        for sid in list(self.active_sagas.keys()):
            if len(self.active_sagas) <= self.max_active_sagas:
                break
            if self.active_sagas[sid].status in terminal:
                del self.active_sagas[sid]

    # ── Lifecycle ────────────────────────────────────────────────────────
    def start_transaction_log(self, saga_id: str, goal: str, tenant_id: str) -> None:
        """Initialize a new saga transaction session."""
        if saga_id in self.active_sagas:
            raise CoordinatorError(f"Saga '{saga_id}' already exists.")
        self.active_sagas[saga_id] = SagaTransaction(
            saga_id=saga_id,
            tenant_id=tenant_id,
            goal=goal,
            status=SagaStatus.RUNNING.value,
            start_time=time.time(),
        )
        metrics.inc("sagas_started")
        logger.info(
            "[SAGA-%s] Transaction initialized for tenant '%s'. Goal: '%s'",
            saga_id,
            tenant_id,
            goal,
        )
        if self.db:
            self.db.write_transaction_state(saga_id, SagaStatus.RUNNING.value, {"goal": goal, "tenant_id": tenant_id})

    # ── Execution ────────────────────────────────────────────────────────
    def execute_saga(
        self,
        saga_id: str,
        steps: list[Any],
        callback: Callable[[Any, str, str], None] | None = None,
    ) -> bool:
        """Execute a sequence of SagaStep tasks.

        Raises ``CoordinatorError`` if ``saga_id`` is unknown (caller must call
        ``start_transaction_log`` first or use the ``/saga/start`` endpoint).

        Returns True on full commit, False when a rollback occurred.
        """
        if saga_id not in self.active_sagas:
            raise CoordinatorError(f"Saga '{saga_id}' not found. Call start_transaction_log first.")

        saga = self.active_sagas[saga_id]
        completed = saga.completed_steps

        for step in steps:
            journaled = False
            execution_ambiguous = False
            # ── Idempotency check ────────────────────────────────────────
            if (
                step.idempotency_key
                and self.db
                and hasattr(self.db, "step_already_committed")
                and self.db.step_already_committed(saga_id, step.idempotency_key)
            ):
                logger.info(
                    "[SAGA-%s] Step '%s' already committed (idempotency_key=%s). Skipping.",
                    saga_id,
                    step.step_name,
                    step.idempotency_key,
                )
                step.status = StepStatus.COMMITTED.value
                continue

            step.status = StepStatus.RUNNING.value
            logger.info("[SAGA-%s] Initiating Step: '%s'", saga_id, step.step_name)
            if callback:
                callback(step, StepStatus.RUNNING.value, "")

            try:
                # 1. Verification Gate
                with span("saga.verify", saga_id=saga_id, step_id=step.step_id), metrics.time("verify_seconds"):
                    ver_ok, explanation = self.verifier.verify(step.action.arguments, step.invariants)
                if not ver_ok:
                    metrics.inc("steps_rejected")
                    step.status = StepStatus.FAILED.value
                    step.error = f"Logic Solver check failed: {explanation}"
                    logger.error(
                        "[SAGA-%s] Invariant violation at step '%s': %s",
                        saga_id,
                        step.step_name,
                        explanation,
                    )
                    if callback:
                        callback(step, StepStatus.FAILED.value, step.error)
                    self.execute_compensations(saga_id, completed, callback)
                    return False

                # Validate registry membership, typed policy, and declared
                # capabilities before the durable journal enters EXECUTING.
                # This keeps deterministic local rejection distinct from an
                # ambiguous crash after an external effect may have started.
                preflight = getattr(self.sandbox, "validate", None)
                if callable(preflight):
                    preflight(step.action)

                # 1b. Human-in-the-loop approval gate
                if step.requires_approval and not step.approved:
                    step.status = StepStatus.AWAITING_APPROVAL.value
                    saga.status = SagaStatus.AWAITING_APPROVAL.value
                    idx = steps.index(step)
                    saga.pending_steps = steps[idx:]
                    logger.info("[SAGA-%s] Step '%s' awaiting human approval.", saga_id, step.step_name)
                    if callback:
                        callback(step, StepStatus.AWAITING_APPROVAL.value, "")
                    if self.db:
                        self.db.write_transaction_state(
                            saga_id,
                            SagaStatus.AWAITING_APPROVAL.value,
                            {
                                "pending_steps": [self._serialize_step(item) for item in saga.pending_steps],
                                "start_time": saga.start_time,
                            },
                        )
                    return False

                # 2. Execute in sandbox
                if self.db and hasattr(self.db, "prepare_effect"):
                    self.db.prepare_effect(
                        saga_id,
                        step.step_id,
                        step.step_name,
                        step.action.tool_name,
                        step.action.arguments,
                        step.compensation.tool_name,
                        step.compensation.arguments,
                        step.idempotency_key,
                    )
                    journaled = True
                    self._checkpoint("effect_prepared", saga_id=saga_id, step_id=step.step_id)
                    self.db.transition_effect(
                        saga_id,
                        step.step_id,
                        EffectState.PREPARED.value,
                        EffectState.EXECUTING.value,
                    )
                    execution_ambiguous = True
                    self._checkpoint("effect_executing", saga_id=saga_id, step_id=step.step_id)
                with (
                    span(
                        "saga.tool.execute",
                        saga_id=saga_id,
                        step_id=step.step_id,
                        tool=step.action.tool_name,
                    ),
                    metrics.time("step_seconds"),
                ):
                    result = self.sandbox.execute(step.action)
                self._checkpoint("effect_executed", saga_id=saga_id, step_id=step.step_id)
                if result is None or getattr(result, "success", True) is not True:
                    detail = getattr(result, "error", None) or getattr(result, "status", "failed result")
                    if journaled:
                        assert self.db is not None
                        self.db.transition_effect(
                            saga_id,
                            step.step_id,
                            EffectState.EXECUTING.value,
                            EffectState.ABORTED.value,
                            error=str(detail),
                        )
                        execution_ambiguous = False
                    raise CoordinatorError(f"Sandbox rejected action: {detail}")
                if journaled:
                    assert self.db is not None
                    self.db.transition_effect(
                        saga_id,
                        step.step_id,
                        EffectState.EXECUTING.value,
                        EffectState.APPLIED.value,
                        result=self._result_data(result),
                    )
                    execution_ambiguous = False
                    self._checkpoint("effect_applied", saga_id=saga_id, step_id=step.step_id)
                step.status = StepStatus.COMMITTED.value
                completed.append(step)

                # Persist compensation + idempotency record
                if journaled:
                    assert self.db is not None
                    self.db.commit_effect(saga_id, step.step_id, step.idempotency_key)
                    self._checkpoint("effect_committed", saga_id=saga_id, step_id=step.step_id)
                elif self.db and hasattr(self.db, "append_compensation"):
                    self.db.append_compensation(saga_id, step.compensation.tool_name, step.compensation.arguments)
                if not journaled and step.idempotency_key and self.db and hasattr(self.db, "mark_step_committed"):
                    self.db.mark_step_committed(saga_id, step.idempotency_key)
                if self.db and hasattr(self.db, "record_step"):
                    self.db.record_step(
                        saga_id,
                        step.step_name,
                        step.action.tool_name,
                        step.action.arguments,
                        getattr(result, "data", {}) if result is not None else {},
                        step.status,
                    )

                logger.info("[SAGA-%s] Step '%s' executed and committed successfully.", saga_id, step.step_name)
                if callback:
                    callback(step, StepStatus.COMMITTED.value, "")

            except Exception as e:
                step.status = StepStatus.FAILED.value
                step.error = f"Runtime Execution Exception: {e!s}"
                logger.error(
                    "[SAGA-%s] Exception at step '%s': %s",
                    saga_id,
                    step.step_name,
                    step.error,
                    exc_info=True,
                )
                if callback:
                    callback(step, StepStatus.FAILED.value, step.error)
                self.execute_compensations(saga_id, completed, callback)
                if execution_ambiguous and journaled:
                    assert self.db is not None
                    ambiguity = "forward effect raised before its outcome was durably recorded"
                    try:
                        effect = self.db.transition_effect(
                            saga_id,
                            step.step_id,
                            EffectState.EXECUTING.value,
                            EffectState.AMBIGUOUS.value,
                            error=ambiguity,
                        )
                    except Exception:  # noqa: BLE001 - preserve the original execution error
                        effect = {"step_name": step.step_name}
                    self._dead_letter_recovery(saga_id, effect, ambiguity)
                return False

        saga.status = SagaStatus.COMMITTED.value
        metrics.inc("sagas_committed")
        if self.db:
            self.db.write_transaction_state(saga_id, SagaStatus.COMMITTED.value, {})
        logger.info("[SAGA-%s] Saga transaction committed successfully.", saga_id)
        self._evict_if_needed()
        return True

    @staticmethod
    def _result_data(result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            return result
        data = getattr(result, "data", None)
        if isinstance(data, dict):
            return data
        return {"status": getattr(result, "status", "SUCCESS")}

    # ── Human-in-the-loop approval ──────────────────────────────────────
    def resume_after_approval(self, saga_id: str, callback: Callable[[Any, str, str], None] | None = None) -> bool:
        """Approve the pending step and resume saga execution."""
        if saga_id not in self.active_sagas:
            raise CoordinatorError(f"Saga '{saga_id}' not found.")
        saga = self.active_sagas[saga_id]
        if saga.status != SagaStatus.AWAITING_APPROVAL.value or not saga.pending_steps:
            raise CoordinatorError(f"Saga '{saga_id}' is not awaiting approval.")
        pending = saga.pending_steps
        saga.pending_steps = []
        pending[0].approved = True
        saga.status = SagaStatus.RUNNING.value
        if self.db:
            self.db.write_transaction_state(saga_id, SagaStatus.RUNNING.value, {"pending_steps": []})
        return self.execute_saga(saga_id, pending, callback)

    def reject_pending(self, saga_id: str, callback: Callable[[Any, str, str], None] | None = None) -> bool:
        """Reject the pending step, rolling back any previously committed steps."""
        if saga_id not in self.active_sagas:
            raise CoordinatorError(f"Saga '{saga_id}' not found.")
        saga = self.active_sagas[saga_id]
        if saga.status != SagaStatus.AWAITING_APPROVAL.value:
            raise CoordinatorError(f"Saga '{saga_id}' is not awaiting approval.")
        for step in saga.pending_steps:
            step.status = StepStatus.ROLLED_BACK.value
        saga.pending_steps = []
        if self.db:
            self.db.write_transaction_state(saga_id, SagaStatus.COMPENSATING.value, {"pending_steps": []})
        self.execute_compensations(saga_id, saga.completed_steps, callback)
        return True

    # ── Compensations ────────────────────────────────────────────────────
    def execute_compensations(
        self,
        saga_id: str,
        completed_steps: list[Any],
        callback: Callable[[Any, str, str], None] | None = None,
    ) -> None:
        """Execute compensations in LIFO order and report incomplete recovery."""
        logger.warning("[SAGA-%s] Initiating rollback for %d completed step(s).", saga_id, len(completed_steps))
        if self.db:
            self.db.write_transaction_state(saga_id, SagaStatus.COMPENSATING.value, {})

        for step in reversed(completed_steps):
            step.status = StepStatus.COMPENSATING.value
            logger.info("[SAGA-%s] Reverting step: '%s'", saga_id, step.step_name)
            if callback:
                callback(step, StepStatus.COMPENSATING.value, "")

            try:
                journaled = bool(self.db and hasattr(self.db, "get_effect"))
                if journaled:
                    assert self.db is not None
                    effect = self.db.get_effect(saga_id, step.step_id)
                    if effect["state"] in {EffectState.APPLIED.value, EffectState.COMMITTED.value}:
                        self.db.transition_effect(
                            saga_id,
                            step.step_id,
                            {EffectState.APPLIED.value, EffectState.COMMITTED.value},
                            EffectState.COMPENSATING.value,
                        )
                    elif effect["state"] != EffectState.COMPENSATING.value:
                        raise CoordinatorError(f"Cannot compensate effect {step.step_id} in state {effect['state']}")
                    self._checkpoint("compensation_executing", saga_id=saga_id, step_id=step.step_id)
                with span(
                    "saga.tool.compensate",
                    saga_id=saga_id,
                    step_id=step.step_id,
                    tool=step.compensation.tool_name,
                ):
                    comp_ok = self.sandbox.execute_compensation(step.compensation)
                self._checkpoint("compensation_executed", saga_id=saga_id, step_id=step.step_id)
                if comp_ok:
                    if journaled:
                        assert self.db is not None
                        self.db.transition_effect(
                            saga_id,
                            step.step_id,
                            EffectState.COMPENSATING.value,
                            EffectState.COMPENSATED.value,
                        )
                        self._checkpoint("compensation_recorded", saga_id=saga_id, step_id=step.step_id)
                    step.status = StepStatus.ROLLED_BACK.value
                    logger.info("[SAGA-%s] Rollback complete for step: '%s'", saga_id, step.step_name)
                    if callback:
                        callback(step, StepStatus.ROLLED_BACK.value, "")
                else:
                    self._handle_compensation_failure(saga_id, step, "Compensation returned False", callback)
                    return
            except Exception as e:
                self._handle_compensation_failure(saga_id, step, str(e), callback)
                return

        if saga_id in self.active_sagas:
            self.active_sagas[saga_id].status = SagaStatus.ROLLED_BACK.value
        metrics.inc("sagas_rolled_back")
        if self.db:
            self.db.write_transaction_state(saga_id, SagaStatus.ROLLED_BACK.value, {})
        logger.warning("[SAGA-%s] Registered compensations completed.", saga_id)

    def _handle_compensation_failure(
        self,
        saga_id: str,
        step: Any,
        error: str,
        callback: Callable[[Any, str, str], None] | None,
    ) -> None:
        step.status = StepStatus.COMPENSATION_FAILED.value
        metrics.inc("compensations_failed")
        logger.error(
            "[SAGA-%s] [CRITICAL] Compensation failed for step '%s': %s — system may be inconsistent.",
            saga_id,
            step.step_name,
            error,
        )
        if callback:
            callback(step, StepStatus.COMPENSATION_FAILED.value, error)
        if saga_id in self.active_sagas:
            self.active_sagas[saga_id].status = SagaStatus.COMPENSATION_FAILED.value
        if self.db:
            self.db.write_transaction_state(
                saga_id,
                SagaStatus.COMPENSATION_FAILED.value,
                {"failed_step": step.step_name},
            )
            if hasattr(self.db, "get_effect"):
                try:
                    effect = self.db.get_effect(saga_id, step.step_id)
                    if effect["state"] == EffectState.COMPENSATING.value:
                        self.db.transition_effect(
                            saga_id,
                            step.step_id,
                            EffectState.COMPENSATING.value,
                            EffectState.COMPENSATION_FAILED.value,
                            error=error,
                        )
                except Exception:  # noqa: BLE001 - do not mask the compensation failure
                    pass
            if hasattr(self.db, "push_dead_letter"):
                self.db.push_dead_letter(saga_id, step.step_name, error)
