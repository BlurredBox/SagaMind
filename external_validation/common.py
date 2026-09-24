"""Framework-neutral workload and scoring utilities.

The scorer inspects filesystem state directly.  Framework adapters only receive an
opaque case identifier and a sequence of operations; they do not receive the
expected outcome or failure-class label.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TrialCase:
    case_id: str
    operations: tuple[dict[str, str], ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TrialCase:
        return cls(value["case_id"], tuple(dict(item) for item in value["operations"]))

    def to_dict(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "operations": list(self.operations)}


def generate_cases(count: int, seed: int) -> list[TrialCase]:
    """Generate a balanced, deterministically shuffled corpus."""
    if count <= 0 or count % 2:
        raise ValueError("case count must be a positive even number")
    rng = random.Random(seed)
    cases: list[TrialCase] = []
    for index in range(count // 2):
        nonce = hashlib.sha256(f"runtime:{seed}:{index}".encode()).hexdigest()[:12]
        cases.append(
            TrialCase(
                case_id=f"case-{hashlib.sha256(f'r:{nonce}'.encode()).hexdigest()[:16]}",
                operations=(
                    {"op": "write", "path": "existing.txt", "content": f"mutated-{nonce}"},
                    {"op": "write", "path": f"new-{nonce}.txt", "content": nonce},
                    {"op": "fail", "path": f"failure-{nonce}.txt", "content": ""},
                ),
            )
        )
    for index in range(count // 2):
        nonce = hashlib.sha256(f"escape:{seed}:{index}".encode()).hexdigest()[:12]
        cases.append(
            TrialCase(
                case_id=f"case-{hashlib.sha256(f'e:{nonce}'.encode()).hexdigest()[:16]}",
                operations=({"op": "write", "path": f"../outside-{nonce}.txt", "content": nonce},),
            )
        )
    rng.shuffle(cases)
    return cases


def classify(case: TrialCase) -> str:
    return "runtime_failure" if any(item["op"] == "fail" for item in case.operations) else "unsafe_path"


def materialize_operations(case: TrialCase, workspace: Path) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    for item in case.operations:
        target = (workspace / item["path"]).resolve()
        existed = target.exists()
        operations.append(
            {
                **item,
                "path": str(target),
                "workspace": str(workspace.resolve()),
                "existed": existed,
                "previous": target.read_text(encoding="utf-8") if existed else "",
            }
        )
    return operations


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def wilson(successes: int, total: int, z: float = 1.959963984540054) -> list[float]:
    if total == 0:
        return [0.0, 0.0]
    rate = successes / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    radius = z * math.sqrt((rate * (1 - rate) + z * z / (4 * total)) / total) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_system: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_system.setdefault(str(row["system"]), []).append(row)
    result: dict[str, Any] = {}
    for system, system_rows in sorted(by_system.items()):
        runtime = [row for row in system_rows if row["failure_class"] == "runtime_failure"]
        unsafe = [row for row in system_rows if row["failure_class"] == "unsafe_path"]
        successes = sum(bool(row["safe_outcome"]) for row in system_rows)
        latencies = sorted(float(row["latency_ms"]) for row in system_rows)
        result[system] = {
            "trials": len(system_rows),
            "safe_outcome_rate": successes / len(system_rows),
            "safe_outcome_rate_95ci": wilson(successes, len(system_rows)),
            "runtime_failure_recovery_rate": sum(bool(row["safe_outcome"]) for row in runtime) / len(runtime),
            "unsafe_path_block_rate": sum(bool(row["safe_outcome"]) for row in unsafe) / len(unsafe),
            "residual_effects": sum(int(row["residual_effects"]) for row in system_rows),
            "unsafe_executions": sum(int(row["unsafe_executions"]) for row in system_rows),
            "median_latency_ms": statistics.median(latencies),
            "p95_latency_ms": latencies[min(len(latencies) - 1, math.ceil(0.95 * len(latencies)) - 1)],
        }
    return result


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()
