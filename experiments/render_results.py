"""Render the paper's result table directly from the evaluation artifact."""

from __future__ import annotations

from pathlib import Path
from typing import Any

START = "<!-- GENERATED_RESULTS:START -->"
END = "<!-- GENERATED_RESULTS:END -->"


def results_table(result: dict[str, Any]) -> str:
    saga = result["saga_fault_injection"]
    smt = result["smt_policy_classification"]
    memory = result["synthetic_memory_clustering"]
    decay = result["decay_properties"]
    variants = result["filesystem_ablations"]["variants"]
    matrix = result["control_failure_matrix"]
    saga_outcome = (
        f"SagaMind {saga['sagamind_restoration_rate']:.3f} restored; "
        f"naive {saga['naive_restoration_rate']:.3f}; "
        f"exact McNemar p={saga['paired_exact_mcnemar_p']:.2e}"
    )
    smt_outcome = (
        f"Accuracy {smt['accuracy']:.3f}; {smt['false_accepts']} false accepts; {smt['false_rejects']} false rejects"
    )
    latency_outcome = f"Median {smt['median_latency_ms']:.2f} ms; p95 {smt['p95_latency_ms']:.2f} ms"
    memory_outcome = (
        f"Mean pairwise F1 {memory['mean_pairwise_f1']:.3f}; mean {memory['mean_cluster_count']:g} clusters"
    )
    decay_checks = decay["time_monotonicity_checks"] + decay["retrieval_monotonicity_checks"]
    decay_violations = decay["time_monotonicity_violations"] + decay["retrieval_monotonicity_violations"]
    rows = [
        ("Saga fault injection", f"{saga['trials']:,} paired trials", saga_outcome),
        ("SMT classification", f"{smt['cases']:,} cases", smt_outcome),
        ("SMT latency", f"{smt['cases']:,} cases", latency_outcome),
        ("Synthetic clustering", f"{memory['repetitions']} datasets", memory_outcome),
        ("Decay properties", f"{decay_checks} checks", f"{decay_violations} monotonicity violations"),
    ]
    labels = {
        "sequential": "Filesystem ablation: sequential",
        "verify_only": "Filesystem ablation: verify-only",
        "saga_only": "Filesystem ablation: Saga-only",
        "full": "Filesystem ablation: full",
    }
    for name, label in labels.items():
        item = variants[name]
        outcome = (
            f"Safe {item['safe_outcome_rate']:.3f}; "
            f"recovery {item['runtime_failure_recovery_rate']:.3f}; "
            f"path block {item['unsafe_path_block_rate']:.3f}; "
            f"residual effects {item['residual_effects']}; "
            f"unsafe executions {item['unsafe_executions']}"
        )
        rows.append(
            (
                label,
                f"{item['trials']} trials",
                outcome,
            )
        )
    rows.append(
        (
            "Failure-semantics matrix",
            f"{matrix['total']} deterministic cases",
            f"{matrix['passed']} passed",
        )
    )
    lines = ["| Component | Sample | Outcome |", "|---|---:|---|"]
    lines.extend(f"| {component} | {sample} | {outcome} |" for component, sample, outcome in rows)
    return "\n".join(lines)


def update_paper(path: Path, result: dict[str, Any]) -> None:
    text = path.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise ValueError(f"Paper {path} has no generated-results markers")
    prefix, remainder = text.split(START, 1)
    _, suffix = remainder.split(END, 1)
    replacement = f"{START}\n{results_table(result)}\n{END}"
    path.write_text(prefix + replacement + suffix, encoding="utf-8")
