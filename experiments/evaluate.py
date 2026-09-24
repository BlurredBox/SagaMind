"""Run SagaMind's deterministic, offline research evaluation.

This suite tests implementation-level hypotheses. It does not evaluate LLM task
quality and must not be used to claim that SagaMind improves agent intelligence.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import logging
import math
import platform
import random
import statistics
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from experiments.ablation import evaluate_filesystem_ablations
from experiments.render_results import update_paper
from src.config import settings
from src.memory.consolidation import MemoryConsolidator
from src.memory.decay import EbbinghausMemoryManager
from src.models import ActionPayload, MemoryNode, SagaStep, SandboxResult
from src.orchestrator.coordinator import InjectedCrash, SagaTransactionCoordinator
from src.orchestrator.state_store import SagaStateStore
from src.verifier.z3_prover import Z3Verifier


@dataclass
class Mutation:
    """One controlled state mutation used by the fault-injection study."""

    step: int


class PassVerifier:
    def verify(self, _arguments: dict[str, Any], _invariant: str) -> tuple[bool, str]:
        return True, "controlled experiment"


class FaultSandbox:
    """Deterministic state machine with one injected forward failure."""

    def __init__(self, fail_at: int):
        self.fail_at = fail_at
        self.state: list[Mutation] = []

    def execute(self, action: ActionPayload) -> SandboxResult:
        step = int(action.arguments["step"])
        if step == self.fail_at:
            raise RuntimeError("injected failure")
        self.state.append(Mutation(step))
        return SandboxResult(success=True)

    def execute_compensation(self, action: ActionPayload) -> bool:
        step = int(action.arguments["step"])
        if not self.state or self.state[-1].step != step:
            return False
        self.state.pop()
        return True


class _CountingSandbox:
    def __init__(self, *, fail_second: bool = False, compensation_ok: bool = True) -> None:
        self.calls = 0
        self.state = 0
        self.fail_second = fail_second
        self.compensation_ok = compensation_ok

    def execute(self, _action: ActionPayload) -> SandboxResult:
        self.calls += 1
        if self.fail_second and self.calls == 2:
            raise RuntimeError("injected second-step failure")
        self.state += 1
        return SandboxResult(success=True, data={"state": self.state})

    def execute_compensation(self, _action: ActionPayload) -> bool:
        if not self.compensation_ok:
            return False
        self.state -= 1
        return True

    def resolve_effect(self, _effect: dict[str, Any]) -> str:
        return "APPLIED"


def _memory_store() -> SagaStateStore:
    previous = settings.state_store_backend
    settings.state_store_backend = "memory"
    try:
        return SagaStateStore()
    finally:
        settings.state_store_backend = previous


def _controlled_step(step_id: str, *, key: str | None = None) -> SagaStep:
    return SagaStep(
        step_id=step_id,
        step_name=step_id,
        action=ActionPayload("MUTATE", {"step": step_id}),
        compensation=ActionPayload("UNDO", {"step": step_id}),
        invariants="",
        idempotency_key=key,
    )


def evaluate_failure_matrix() -> dict[str, Any]:
    """Exercise failure semantics omitted from the repeated ablation workload."""
    false_result_sandbox = _CountingSandbox()
    false_result_sandbox.execute = lambda _action: SandboxResult(success=False, error="injected false result")
    false_result = SagaTransactionCoordinator(PassVerifier(), false_result_sandbox)
    false_result.start_transaction_log("false-result", "failure matrix", "experiment")
    false_result_blocked = not false_result.execute_saga("false-result", [_controlled_step("false")])

    duplicate_store = _memory_store()
    duplicate_sandbox = _CountingSandbox()
    duplicate = SagaTransactionCoordinator(PassVerifier(), duplicate_sandbox, db_client=duplicate_store)
    duplicate.start_transaction_log("duplicate", "failure matrix", "experiment")
    duplicate.execute_saga(
        "duplicate",
        [_controlled_step("first", key="same-effect"), _controlled_step("repeat", key="same-effect")],
    )
    duplicate_suppressed = duplicate_sandbox.calls == 1 and duplicate_sandbox.state == 1

    crash_store = _memory_store()
    crash_sandbox = _CountingSandbox()

    def crash_after_effect(name: str, _context: dict[str, Any]) -> None:
        if name == "effect_executed":
            raise InjectedCrash("injected process death")

    crashing = SagaTransactionCoordinator(
        PassVerifier(), crash_sandbox, db_client=crash_store, fault_injector=crash_after_effect
    )
    crashing.start_transaction_log("crash", "failure matrix", "experiment")
    with contextlib.suppress(InjectedCrash):
        crashing.execute_saga("crash", [_controlled_step("crash-step", key="crash-key")])
    recovered = SagaTransactionCoordinator(PassVerifier(), crash_sandbox, db_client=crash_store).recover()
    crash_recovered = recovered == 1 and crash_sandbox.state == 0 and not crash_store.list_dead_letters()

    compensation_store = _memory_store()
    compensation_sandbox = _CountingSandbox(fail_second=True, compensation_ok=False)
    compensation = SagaTransactionCoordinator(PassVerifier(), compensation_sandbox, db_client=compensation_store)
    compensation.start_transaction_log("comp-failure", "failure matrix", "experiment")
    compensation.execute_saga(
        "comp-failure",
        [_controlled_step("applied"), _controlled_step("fails")],
    )
    compensation_escalated = bool(compensation_store.list_dead_letters())

    outcomes = {
        "false_result_rejected": false_result_blocked,
        "duplicate_idempotency_suppressed": duplicate_suppressed,
        "crash_boundary_recovered": crash_recovered,
        "compensation_failure_dead_lettered": compensation_escalated,
    }
    return {
        "scope": (
            "Deterministic one-case semantic checks; repeated exception and unsafe-path cases "
            "are in filesystem_ablations."
        ),
        "outcomes": outcomes,
        "passed": sum(outcomes.values()),
        "total": len(outcomes),
    }


def _wilson(successes: int, total: int, z: float = 1.959963984540054) -> list[float]:
    if total == 0:
        return [0.0, 0.0]
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    radius = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denom
    return [max(0.0, center - radius), min(1.0, center + radius)]


def _mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value for discordant paired outcomes."""
    n = b + c
    if n == 0:
        return 1.0
    tail = min(b, c)
    probability = sum(math.comb(n, k) for k in range(tail + 1)) / (2**n)
    return min(1.0, 2.0 * probability)


def evaluate_saga(trials: int, seed: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    saga_clean = 0
    naive_clean = 0
    b = 0
    c = 0
    for trial in range(trials):
        length = rng.randint(2, 12)
        fail_at = rng.randint(1, length - 1)

        naive_state = list(range(fail_at))
        naive_restored = len(naive_state) == 0

        sandbox = FaultSandbox(fail_at)
        coordinator = SagaTransactionCoordinator(PassVerifier(), sandbox)
        saga_id = f"fault-{trial}"
        coordinator.start_transaction_log(saga_id, "fault injection", "experiment")
        steps = [
            SagaStep(
                step_id=f"{trial}-{step}",
                step_name=f"mutate-{step}",
                action=ActionPayload("MUTATE", {"step": step}),
                compensation=ActionPayload("UNDO", {"step": step}),
                invariants="",
            )
            for step in range(length)
        ]
        committed = coordinator.execute_saga(saga_id, steps)
        saga_restored = not committed and not sandbox.state
        saga_clean += int(saga_restored)
        naive_clean += int(naive_restored)
        b += int(saga_restored and not naive_restored)
        c += int(naive_restored and not saga_restored)
        rows.append(
            {
                "trial": trial,
                "workflow_length": length,
                "failure_position": fail_at,
                "sagamind_restored": saga_restored,
                "naive_restored": naive_restored,
                "sagamind_residual_mutations": len(sandbox.state),
                "naive_residual_mutations": len(naive_state),
            }
        )
    return (
        {
            "hypothesis": "LIFO compensation restores controlled state after an injected forward failure.",
            "trials": trials,
            "sagamind_restoration_rate": saga_clean / trials,
            "sagamind_restoration_rate_95ci": _wilson(saga_clean, trials),
            "naive_restoration_rate": naive_clean / trials,
            "naive_restoration_rate_95ci": _wilson(naive_clean, trials),
            "paired_exact_mcnemar_p": _mcnemar_exact(b, c),
            "discordant_sagamind_only": b,
            "discordant_naive_only": c,
        },
        rows,
    )


def evaluate_verifier(cases: int, seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    verifier = Z3Verifier()
    invariant = "(assert (and (>= amount 0) (<= amount balance) (=> (> amount 1000) privileged)))"
    correct = false_accepts = false_rejects = 0
    latencies: list[float] = []
    for _ in range(cases):
        amount = rng.randint(-500, 5000)
        balance = rng.randint(0, 5000)
        privileged = bool(rng.getrandbits(1))
        expected_safe = amount >= 0 and amount <= balance and (amount <= 1000 or privileged)
        started = time.perf_counter_ns()
        actual_safe, _ = verifier.verify({"amount": amount, "balance": balance, "privileged": privileged}, invariant)
        latencies.append((time.perf_counter_ns() - started) / 1_000_000)
        correct += int(actual_safe == expected_safe)
        false_accepts += int(actual_safe and not expected_safe)
        false_rejects += int(not actual_safe and expected_safe)
    ordered = sorted(latencies)
    p95 = ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]
    return {
        "hypothesis": "Concrete arithmetic policies are classified consistently with their oracle.",
        "solver_active": verifier.z3_active,
        "cases": cases,
        "accuracy": correct / cases,
        "accuracy_95ci": _wilson(correct, cases),
        "false_accepts": false_accepts,
        "false_rejects": false_rejects,
        "median_latency_ms": statistics.median(latencies),
        "p95_latency_ms": p95,
    }


def _pairwise_f1(true_labels: list[int], predicted_labels: list[int]) -> float:
    tp = fp = fn = 0
    for i in range(len(true_labels)):
        for j in range(i + 1, len(true_labels)):
            same_true = true_labels[i] == true_labels[j] and true_labels[i] >= 0
            same_pred = predicted_labels[i] == predicted_labels[j] and predicted_labels[i] >= 0
            tp += int(same_true and same_pred)
            fp += int(not same_true and same_pred)
            fn += int(same_true and not same_pred)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _mean_ci(values: list[float]) -> list[float]:
    if len(values) < 2:
        return [values[0], values[0]]
    mean = statistics.mean(values)
    radius = 1.959963984540054 * statistics.stdev(values) / math.sqrt(len(values))
    return [mean - radius, mean + radius]


def evaluate_clustering(repetitions: int, seed: int) -> dict[str, Any]:
    import numpy as np

    scores: list[float] = []
    cluster_counts: list[int] = []
    for repetition in range(repetitions):
        rng = np.random.default_rng(seed + repetition)
        episodes: list[dict[str, Any]] = []
        true_labels: list[int] = []
        for label in range(4):
            center = np.zeros(32)
            center[label] = 1.0
            for item in range(20):
                vector = center + rng.normal(0.0, 0.025, size=32)
                episodes.append(
                    {
                        "memory_id": f"{repetition}-{label}-{item}",
                        "summary": "synthetic",
                        "agent_role": "experiment",
                        "embedding": vector.tolist(),
                    }
                )
                true_labels.append(label)
        consolidator = MemoryConsolidator(None, None)
        clusters = consolidator._cluster(episodes, eps=0.08, min_samples=3)
        predicted = [-1] * len(episodes)
        episode_index = {id(episode): index for index, episode in enumerate(episodes)}
        for label, members in clusters.items():
            for member in members:
                predicted[episode_index[id(member)]] = label
        scores.append(_pairwise_f1(true_labels, predicted))
        cluster_counts.append(len(clusters))
    return {
        "hypothesis": "Deterministic cosine-DBSCAN recovers separated synthetic concept groups.",
        "dataset": "Four 32-dimensional Gaussian concept groups; 20 samples per group.",
        "repetitions": repetitions,
        "mean_pairwise_f1": statistics.mean(scores),
        "mean_pairwise_f1_95ci": _mean_ci(scores),
        "min_pairwise_f1": min(scores),
        "mean_cluster_count": statistics.mean(cluster_counts),
    }


def evaluate_decay() -> dict[str, Any]:
    manager = EbbinghausMemoryManager()
    now = datetime.now(timezone.utc)
    time_checks = retrieval_checks = 0
    time_violations = retrieval_violations = 0
    for importance in (0.1, 0.3, 0.5, 0.8, 1.0):
        for retrievals in (0, 1, 2, 5, 10, 100):
            previous = 1.0
            for hours in (0, 1, 6, 12, 24, 72, 168, 720):
                node = MemoryNode(
                    memory_id="grid",
                    created_at=now,
                    last_retrieved_at=now - timedelta(hours=hours),
                    agent_role="experiment",
                    summary="grid",
                    importance_score=importance,
                    retrieval_count=retrievals,
                )
                current = manager.calculate_retention(node, now=now)
                time_checks += 1
                time_violations += int(current > previous + 1e-12)
                previous = current
        for hours in (1, 12, 72, 720):
            previous = 0.0
            for retrievals in (0, 1, 2, 5, 10, 100):
                node = MemoryNode(
                    memory_id="grid",
                    created_at=now,
                    last_retrieved_at=now - timedelta(hours=hours),
                    agent_role="experiment",
                    summary="grid",
                    importance_score=importance,
                    retrieval_count=retrievals,
                )
                current = manager.calculate_retention(node, now=now)
                retrieval_checks += 1
                retrieval_violations += int(current + 1e-12 < previous)
                previous = current
    return {
        "hypothesis": "Retention is non-increasing in elapsed time and non-decreasing in retrieval count.",
        "time_monotonicity_checks": time_checks,
        "time_monotonicity_violations": time_violations,
        "retrieval_monotonicity_checks": retrieval_checks,
        "retrieval_monotonicity_violations": retrieval_violations,
        "note": "Mathematical property check; not evidence of improved downstream memory quality.",
    }


def _git_revision() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _working_tree_dirty() -> bool | None:
    try:
        completed = subprocess.run(
            ["git", "diff", "--quiet", "--", "."],
            check=False,
        )
        untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], text=True).strip()
        return completed.returncode != 0 or bool(untracked)
    except (OSError, subprocess.CalledProcessError):
        return None


def run(
    output_dir: Path,
    trials: int,
    verifier_cases: int,
    repetitions: int,
    seed: int,
    ablation_trials: int = 200,
) -> dict[str, Any]:
    logging.disable(logging.CRITICAL)
    output_dir.mkdir(parents=True, exist_ok=True)
    saga, rows = evaluate_saga(trials, seed)
    filesystem_ablations = evaluate_filesystem_ablations(ablation_trials)
    ablation_rows = filesystem_ablations.pop("raw_trials")
    result = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "revision": _git_revision(),
        "working_tree_dirty": _working_tree_dirty(),
        "seed": seed,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor(),
        },
        "scope": "Controlled component evaluation; no LLM or human-subject evaluation.",
        "saga_fault_injection": saga,
        "smt_policy_classification": evaluate_verifier(verifier_cases, seed + 1),
        "synthetic_memory_clustering": evaluate_clustering(repetitions, seed + 2),
        "decay_properties": evaluate_decay(),
        "filesystem_ablations": filesystem_ablations,
        "control_failure_matrix": evaluate_failure_matrix(),
    }
    (output_dir / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    with (output_dir / "saga_trials.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (output_dir / "filesystem_ablation_trials.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ablation_rows[0]))
        writer.writeheader()
        writer.writerows(ablation_rows)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("experiments/results"))
    parser.add_argument("--trials", type=int, default=1000)
    parser.add_argument("--verifier-cases", type=int, default=1000)
    parser.add_argument("--repetitions", type=int, default=30)
    parser.add_argument("--ablation-trials", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20260924)
    args = parser.parse_args()
    if min(args.trials, args.verifier_cases, args.repetitions, args.ablation_trials) <= 0:
        parser.error("all sample counts must be positive")
    result = run(
        args.output,
        args.trials,
        args.verifier_cases,
        args.repetitions,
        args.seed,
        args.ablation_trials,
    )
    update_paper(Path("research_paper.md"), result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
