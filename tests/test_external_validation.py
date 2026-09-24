import csv

from external_validation.common import canonical_hash, classify, generate_cases, summarize
from external_validation.verify_results import verify


def test_corpus_is_deterministic_balanced_and_opaque():
    first = generate_cases(20, 7)
    second = generate_cases(20, 7)
    assert canonical_hash([case.to_dict() for case in first]) == canonical_hash([case.to_dict() for case in second])
    assert sum(classify(case) == "runtime_failure" for case in first) == 10
    assert all("runtime" not in case.case_id and "escape" not in case.case_id for case in first)


def test_summary_and_secondary_verifier(tmp_path):
    rows = [
        {
            "system": system,
            "case_id": case,
            "failure_class": failure,
            "committed": False,
            "safe_outcome": safe,
            "residual_effects": int(not safe),
            "unsafe_executions": int(failure == "unsafe_path" and not safe),
            "latency_ms": 1.0,
        }
        for system in ("a", "b")
        for case, failure, safe in (("1", "runtime_failure", True), ("2", "unsafe_path", system == "a"))
    ]
    aggregates = summarize(rows)
    assert aggregates["a"]["safe_outcome_rate"] == 1.0
    assert aggregates["b"]["safe_outcome_rate"] == 0.5
    results = tmp_path / "results"
    results.mkdir()
    (results / "summary.json").write_text(__import__("json").dumps({"aggregates": aggregates}), encoding="utf-8")
    with (results / "trials.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    assert verify(results)["verified"] is True
