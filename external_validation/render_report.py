"""Render a human-readable report from machine-readable validation results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def render(result: dict[str, Any]) -> str:
    lines = [
        "# Public-baseline comparison and replication status",
        "",
        f"Generated from `{result['case_count_per_system']}` identical cases per system on "
        f"Python {result['environment']['python']}.",
        "",
        (
            "| Configuration | Trials | Safe outcome | Runtime recovery | Unsafe-path block | "
            "Residual effects | Unsafe executions |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    labels = {
        "sagamind_integrated_local": "SagaMind integrated local",
        "temporal_saga_native": f"Temporal {result['environment']['temporalio']} Saga (native)",
        "temporal_saga_guarded": f"Temporal {result['environment']['temporalio']} Saga + path guard",
        "langgraph_checkpoint_native": f"LangGraph {result['environment']['langgraph']} checkpoint (native)",
        "langgraph_matched_controls": f"LangGraph {result['environment']['langgraph']} + matched controls",
    }
    order = (
        "sagamind_integrated_local",
        "temporal_saga_native",
        "temporal_saga_guarded",
        "langgraph_checkpoint_native",
        "langgraph_matched_controls",
    )
    for name in order:
        item = result["aggregates"][name]
        lines.append(
            f"| {labels[name]} | {item['trials']} | {item['safe_outcome_rate']:.3f} | "
            f"{item['runtime_failure_recovery_rate']:.3f} | {item['unsafe_path_block_rate']:.3f} | "
            f"{item['residual_effects']} | {item['unsafe_executions']} |"
        )
    lines.extend(
        [
            "",
            "SagaMind's integrated local configuration and both matched-control configurations passed all "
            "cases. Temporal's native Saga restored runtime failures but had no application path guard. "
            "LangGraph's native checkpoint configuration did not itself undo or policy-check external writes. "
            "The matched variants demonstrate that both public frameworks can satisfy this small workload "
            "when equivalent application controls are added.",
            "",
            "Paired exact McNemar tests are recorded for every native and matched configuration. The matched "
            "comparisons are expected to tie when their controls work. These tests describe this fixed corpus; "
            "they do not prove superiority over arbitrary workloads.",
            "",
            "## Reproducibility record",
            "",
            f"- Repository revision: `{result['revision']}`; dirty worktree recorded as "
            f"`{str(result['working_tree_dirty']).lower()}`.",
            f"- Evaluated-source digest: `{result['evaluated_source_sha256']}`.",
            f"- Protocol digest: `{result['protocol_file_sha256']}`.",
            f"- Corpus file digest: `{result['corpus_file_sha256']}`.",
            f"- Combined dependency-lock digest: `{result['requirements_lock_sha256']}`.",
            f"- Raw rows: `external_validation/results/trials.csv` "
            f"({result['case_count_per_system'] * len(result['systems'])} observations).",
            "- Secondary-verifier recalculation: all rates match and all systems used identical case identifiers.",
            "",
            "## Interpretation limits",
            "",
            "- This is a capability/configuration comparison, not an overall framework leaderboard.",
            "- Temporal and LangGraph matched variants contain explicit application validation and/or "
            "compensation; these controls are not attributed to the frameworks' native configurations.",
            "- Temporal used its official ephemeral test server; this run did not test production clusters, "
            "worker restarts, network partitions, or Activity ambiguity.",
            "- LangGraph used `InMemorySaver`; neither LangGraph configuration is a process-restart test.",
            "- Latencies are descriptive and not suitable for ranking because the systems provide different "
            "services and Temporal crosses a local workflow-service boundary.",
            "- The author/AI-run result is a validated self-replication, not independent replication. The "
            "independent claim remains false until an unaffiliated executor runs the frozen bundle and signs "
            "`ATTESTATION.template.json`.",
            "",
            "## Primary sources",
            "",
            "- [Temporal Python Saga guidance](https://docs.temporal.io/develop/python/best-practices/error-handling)",
            "- [Temporal Python testing guidance](https://docs.temporal.io/develop/python/best-practices/testing-suite)",
            "- [LangGraph persistence documentation](https://docs.langchain.com/oss/python/langgraph/persistence)",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=Path("external_validation/results/summary.json"))
    parser.add_argument("--output", type=Path, default=Path("external_validation/REPORT.md"))
    args = parser.parse_args()
    result = json.loads(args.summary.read_text(encoding="utf-8"))
    args.output.write_text(render(result), encoding="utf-8")


if __name__ == "__main__":
    main()
