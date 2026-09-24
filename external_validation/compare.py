"""Execute the frozen workload against SagaMind and named public baselines."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import tempfile
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from external_validation.adapters import (
    TemporalRunner,
    run_langgraph_matched,
    run_langgraph_native,
    run_sagamind_integrated,
)
from external_validation.common import (
    TrialCase,
    canonical_hash,
    classify,
    materialize_operations,
    summarize,
    tree_digest,
)


def _revision() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _working_tree_dirty() -> bool | None:
    try:
        changed = subprocess.run(["git", "diff", "--quiet", "--", "."], check=False).returncode != 0
        untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], text=True)
        return changed or bool(untracked.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_sha256() -> str:
    paths = [
        Path("external_validation/adapters.py"),
        Path("external_validation/common.py"),
        Path("external_validation/compare.py"),
        Path("external_validation/temporal_baseline.py"),
        Path("src/config.py"),
        Path("src/models.py"),
        Path("src/orchestrator/coordinator.py"),
        Path("src/orchestrator/state_store.py"),
        Path("src/orchestrator/sandbox.py"),
        Path("src/orchestrator/sandbox_worker.py"),
        Path("src/policy.py"),
        Path("src/verifier/z3_prover.py"),
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _load_cases(path: Path) -> list[TrialCase]:
    return [TrialCase.from_dict(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _residual_effects(workspace: Path) -> int:
    existing = workspace / "existing.txt"
    changed_seed = int(not existing.is_file() or existing.read_text(encoding="utf-8") != "original")
    extra_files = sum(1 for path in workspace.rglob("*") if path.is_file() and path != existing)
    return changed_seed + extra_files


async def _one_trial(
    system: str,
    case: TrialCase,
    execute: Callable[[list[dict[str, Any]], str], Awaitable[bool]],
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="sagamind-public-baseline-") as parent_text:
        parent = Path(parent_text)
        workspace = parent / "workspace"
        workspace.mkdir()
        (workspace / "existing.txt").write_text("original", encoding="utf-8")
        before = tree_digest(workspace)
        operations = materialize_operations(case, workspace)
        started = time.perf_counter_ns()
        committed = await execute(operations, case.case_id)
        latency_ms = (time.perf_counter_ns() - started) / 1_000_000
        failure_class = classify(case)
        if failure_class == "runtime_failure":
            safe = tree_digest(workspace) == before
            residual = _residual_effects(workspace)
            unsafe_executions = 0
        else:
            target = Path(str(operations[0]["path"]))
            safe = not target.exists()
            residual = 0
            unsafe_executions = int(target.exists())
        return {
            "system": system,
            "case_id": case.case_id,
            "failure_class": failure_class,
            "committed": committed,
            "safe_outcome": safe,
            "residual_effects": residual,
            "unsafe_executions": unsafe_executions,
            "latency_ms": latency_ms,
        }


async def _sync_adapter(
    function: Callable[[list[dict[str, Any]], str], bool],
    operations: list[dict[str, Any]],
    case_id: str,
) -> bool:
    return function(operations, case_id)


def _mcnemar_exact(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> dict[str, Any]:
    left_by_id = {row["case_id"]: bool(row["safe_outcome"]) for row in left}
    right_by_id = {row["case_id"]: bool(row["safe_outcome"]) for row in right}
    if left_by_id.keys() != right_by_id.keys():
        raise ValueError("paired systems did not run identical case identifiers")
    left_only = sum(left_by_id[key] and not right_by_id[key] for key in left_by_id)
    right_only = sum(right_by_id[key] and not left_by_id[key] for key in left_by_id)
    discordant = left_only + right_only
    if not discordant:
        p_value = 1.0
    else:
        tail = min(left_only, right_only)
        probability = sum(__import__("math").comb(discordant, k) for k in range(tail + 1)) / (2**discordant)
        p_value = min(1.0, 2.0 * probability)
    return {"sagamind_only": left_only, "baseline_only": right_only, "exact_two_sided_p": p_value}


async def run(output: Path, corpus: Path) -> dict[str, Any]:
    cases = _load_cases(corpus)
    if not cases or len(cases) % 2:
        raise ValueError("corpus must contain a non-empty even number of cases")
    rows: list[dict[str, Any]] = []

    async def sagamind(operations: list[dict[str, Any]], case_id: str) -> bool:
        return await _sync_adapter(run_sagamind_integrated, operations, case_id)

    async def langgraph_native(operations: list[dict[str, Any]], case_id: str) -> bool:
        return await _sync_adapter(run_langgraph_native, operations, case_id)

    async def langgraph_matched(operations: list[dict[str, Any]], case_id: str) -> bool:
        return await _sync_adapter(run_langgraph_matched, operations, case_id)

    for case in cases:
        rows.append(await _one_trial("sagamind_integrated_local", case, sagamind))
    for case in cases:
        rows.append(await _one_trial("langgraph_checkpoint_native", case, langgraph_native))
    for case in cases:
        rows.append(await _one_trial("langgraph_matched_controls", case, langgraph_matched))
    async with TemporalRunner() as temporal:
        for case in cases:
            rows.append(await _one_trial("temporal_saga_native", case, temporal.run_native))
        for case in cases:
            rows.append(await _one_trial("temporal_saga_guarded", case, temporal.run_guarded))

    aggregates = summarize(rows)
    by_system = {name: [row for row in rows if row["system"] == name] for name in aggregates}
    result: dict[str, Any] = {
        "schema_version": 2,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "revision": _revision(),
        "working_tree_dirty": _working_tree_dirty(),
        "evaluated_source_sha256": _source_sha256(),
        "protocol_file_sha256": _file_sha256(Path("external_validation/protocol_v2.json")),
        "corpus_file_sha256": _file_sha256(corpus),
        "requirements_lock_sha256": _file_sha256(Path("external_validation/requirements.lock")),
        "corpus_sha256": canonical_hash([case.to_dict() for case in cases]),
        "case_count_per_system": len(cases),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "temporalio": importlib.metadata.version("temporalio"),
            "langgraph": importlib.metadata.version("langgraph"),
        },
        "scope": (
            "Native capability comparison plus matched application-control sensitivity analysis "
            "on filesystem failure semantics; not a general framework leaderboard."
        ),
        "systems": {
            "sagamind_integrated_local": {
                "configuration": (
                    "SagaMind coordinator, Z3 invariant gate, typed tool policy, in-memory effect journal, "
                    "and isolated built-in worker with exact RESTORE_FILE compensation"
                ),
                "capabilities_under_test": [
                    "compensation",
                    "pre-effect path verification",
                    "typed tool policy",
                    "isolated worker",
                    "effect journal lifecycle",
                ],
                "not_configured": ["restart-durable external journal backend"],
            },
            "temporal_saga_native": {
                "configuration": "Temporal Saga pattern with reverse-order Activity compensations",
                "capabilities_under_test": ["compensation"],
                "not_configured": ["application path policy"],
            },
            "temporal_saga_guarded": {
                "configuration": "Temporal Saga pattern plus application path guard",
                "capabilities_under_test": ["compensation", "application path policy"],
            },
            "langgraph_checkpoint_native": {
                "configuration": "LangGraph StateGraph with InMemorySaver checkpoints",
                "capabilities_under_test": ["graph-state checkpointing"],
                "not_configured": ["external-effect compensation", "application path policy"],
            },
            "langgraph_matched_controls": {
                "configuration": "LangGraph checkpoint graph plus application path guard and preimage rollback loop",
                "capabilities_under_test": [
                    "graph-state checkpointing",
                    "external-effect compensation",
                    "application path policy",
                ],
            },
        },
        "aggregates": aggregates,
        "paired_vs_sagamind": {
            baseline: _mcnemar_exact(by_system["sagamind_integrated_local"], by_system[baseline])
            for baseline in (
                "temporal_saga_native",
                "temporal_saga_guarded",
                "langgraph_checkpoint_native",
                "langgraph_matched_controls",
            )
        },
        "acceptance": {
            "same_cases_for_every_system": all(len(items) == len(cases) for items in by_system.values()),
            "balanced_failure_classes": sum(classify(case) == "runtime_failure" for case in cases) == len(cases) // 2,
            "sagamind_integrated_passes_both_failure_classes": (
                aggregates["sagamind_integrated_local"]["safe_outcome_rate"] == 1.0
            ),
            "temporal_matched_controls_pass_both_failure_classes": (
                aggregates["temporal_saga_guarded"]["safe_outcome_rate"] == 1.0
            ),
            "langgraph_matched_controls_pass_both_failure_classes": (
                aggregates["langgraph_matched_controls"]["safe_outcome_rate"] == 1.0
            ),
        },
    }
    result["acceptance"]["all_passed"] = all(result["acceptance"].values())
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    with (output / "trials.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    if not result["acceptance"]["all_passed"]:
        raise RuntimeError("external validation acceptance criteria failed")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=Path("external_validation/trial_corpus.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("external_validation/results"))
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.output, args.corpus)), indent=2))


if __name__ == "__main__":
    main()
