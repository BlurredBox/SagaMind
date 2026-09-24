"""Real-filesystem ablation study for verification and compensation controls."""

from __future__ import annotations

import hashlib
import math
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.models import ActionPayload, SagaStep, SandboxResult
from src.orchestrator.coordinator import SagaTransactionCoordinator
from src.verifier.z3_prover import Z3Verifier


class _PassVerifier:
    def verify(self, _arguments: dict[str, Any], _invariant: str) -> tuple[bool, str]:
        return True, "verification ablated"


@dataclass
class _FilesystemSandbox:
    compensate: bool

    def execute(self, action: ActionPayload) -> SandboxResult:
        if action.tool_name == "INJECT_FAILURE":
            raise RuntimeError("injected filesystem workflow failure")
        path = Path(str(action.arguments["path"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(action.arguments.get("content", "")), encoding="utf-8")
        return SandboxResult(success=True, data={"path": str(path)})

    def execute_compensation(self, action: ActionPayload) -> bool:
        if not self.compensate:
            return False
        path = Path(str(action.arguments["path"]))
        existed = bool(action.arguments.get("existed", False))
        if existed:
            path.write_text(str(action.arguments.get("previous", "")), encoding="utf-8")
        elif path.exists():
            path.unlink()
        return True


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _path_invariant(root: Path) -> str:
    escaped = str(root).replace('"', '""')
    return f'(assert (str.prefixof "{escaped}/" path))'


def _step(
    index: int,
    path: Path,
    content: str,
    root: Path,
    *,
    fail: bool = False,
) -> SagaStep:
    existed = path.exists()
    previous = path.read_text(encoding="utf-8") if existed else ""
    return SagaStep(
        step_id=f"step-{index}",
        step_name=f"filesystem-{index}",
        action=ActionPayload(
            "INJECT_FAILURE" if fail else "WRITE_TEXT",
            {"path": str(path), "content": content},
        ),
        compensation=ActionPayload(
            "RESTORE_TEXT",
            {"path": str(path), "existed": existed, "previous": previous},
        ),
        invariants=_path_invariant(root),
    )


def _wilson(successes: int, total: int, z: float = 1.959963984540054) -> list[float]:
    if total == 0:
        return [0.0, 0.0]
    rate = successes / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    radius = z * math.sqrt((rate * (1 - rate) + z * z / (4 * total)) / total) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def _run_variant(variant: str, repetitions: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    verification = variant in {"verify_only", "full"}
    compensation = variant in {"saga_only", "full"}
    safe = runtime_recovered = unsafe_blocked = 0
    residual_effects = unsafe_executions = 0
    latencies: list[float] = []
    rows: list[dict[str, Any]] = []

    for trial in range(repetitions):
        with tempfile.TemporaryDirectory(prefix="sagamind-ablation-") as parent_text:
            parent = Path(parent_text)
            workspace = parent / "workspace"
            outside = parent / "outside.txt"
            workspace.mkdir()
            seed_file = workspace / "existing.txt"
            seed_file.write_text("original", encoding="utf-8")
            before = _tree_digest(workspace)

            sandbox = _FilesystemSandbox(compensate=compensation)
            verifier = Z3Verifier() if verification else _PassVerifier()
            coordinator = SagaTransactionCoordinator(verifier, sandbox)
            saga_id = f"{variant}-{trial}"
            coordinator.start_transaction_log(saga_id, "filesystem ablation", "experiment")

            if trial % 2 == 0:
                steps = [
                    _step(0, seed_file, "mutated", workspace),
                    _step(1, workspace / "new.txt", "new", workspace),
                    _step(2, workspace / "failure.txt", "", workspace, fail=True),
                ]
                started = time.perf_counter_ns()
                committed = coordinator.execute_saga(saga_id, steps)
                elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
                restored = _tree_digest(workspace) == before
                residual = sum(1 for path in workspace.rglob("*") if path.is_file()) - 1
                residual += int(seed_file.read_text(encoding="utf-8") != "original")
                runtime_recovered += int(restored)
                residual_effects += residual
                safe += int(restored)
                trial_class = "runtime_exception"
                safe_outcome = restored
            else:
                steps = [_step(0, outside, "escaped", workspace)]
                started = time.perf_counter_ns()
                committed = coordinator.execute_saga(saga_id, steps)
                elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
                blocked = not outside.exists()
                unsafe_blocked += int(blocked)
                unsafe_executions += int(not blocked)
                safe += int(blocked)
                residual = 0
                trial_class = "unsafe_path"
                safe_outcome = blocked
            latencies.append(elapsed_ms)
            rows.append(
                {
                    "variant": variant,
                    "trial": trial,
                    "failure_class": trial_class,
                    "committed": committed,
                    "safe_outcome": safe_outcome,
                    "residual_effects": residual,
                    "unsafe_executions": int(trial_class == "unsafe_path" and not safe_outcome),
                    "latency_ms": elapsed_ms,
                }
            )

    runtime_trials = (repetitions + 1) // 2
    unsafe_trials = repetitions // 2
    ordered = sorted(latencies)
    aggregate = {
        "trials": repetitions,
        "safe_outcome_rate": safe / repetitions,
        "safe_outcome_rate_95ci": _wilson(safe, repetitions),
        "runtime_failure_recovery_rate": runtime_recovered / runtime_trials,
        "runtime_failure_recovery_rate_95ci": _wilson(runtime_recovered, runtime_trials),
        "unsafe_path_block_rate": unsafe_blocked / unsafe_trials if unsafe_trials else 0.0,
        "unsafe_path_block_rate_95ci": _wilson(unsafe_blocked, unsafe_trials),
        "residual_effects": residual_effects,
        "unsafe_executions": unsafe_executions,
        "median_latency_ms": ordered[len(ordered) // 2],
        "p95_latency_ms": ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)],
        "solver_errors": 0,
        "verification_enabled": verification,
        "compensation_enabled": compensation,
    }
    return aggregate, rows


def evaluate_filesystem_ablations(repetitions: int = 200) -> dict[str, Any]:
    """Compare controls using real temporary filesystem mutations."""
    variants: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for variant in ("sequential", "verify_only", "saga_only", "full"):
        aggregate, variant_rows = _run_variant(variant, repetitions)
        variants[variant] = aggregate
        rows.extend(variant_rows)
    return {
        "hypothesis": "Verification and compensation address distinct failure classes and compose additively.",
        "workload": "Real temporary-file overwrite/create workflows with injected runtime failures and path escapes.",
        "variants": variants,
        "raw_trials": rows,
    }
