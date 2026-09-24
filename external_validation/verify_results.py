"""Independent standard-library recalculation of a comparison result."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def verify(results: Path) -> dict[str, object]:
    summary_path = results / "summary.json"
    trials_path = results / "trials.csv"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    rows = list(csv.DictReader(trials_path.open(encoding="utf-8", newline="")))
    counts: dict[str, int] = defaultdict(int)
    safe: dict[str, int] = defaultdict(int)
    ids: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        system = row["system"]
        counts[system] += 1
        safe[system] += int(row["safe_outcome"].lower() == "true")
        ids[system].add(row["case_id"])
    recalculated = {system: safe[system] / counts[system] for system in counts}
    rates_match = all(
        abs(recalculated[system] - summary["aggregates"][system]["safe_outcome_rate"]) < 1e-12 for system in counts
    )
    same_cases = len({frozenset(case_ids) for case_ids in ids.values()}) == 1
    artifact_hash = hashlib.sha256(summary_path.read_bytes() + trials_path.read_bytes()).hexdigest()
    result = {
        "verified": rates_match and same_cases,
        "rates_match": rates_match,
        "same_case_ids": same_cases,
        "row_count": len(rows),
        "safe_outcome_rates": recalculated,
        "result_artifact_sha256": artifact_hash,
    }
    if not result["verified"]:
        raise SystemExit(json.dumps(result, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("external_validation/results"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.results)
    output = args.output or args.results / "verification.json"
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
