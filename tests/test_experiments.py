"""Smoke tests for the reproducible evaluation artifact."""

import json

from experiments.evaluate import run


def test_evaluation_writes_reproducible_artifacts(tmp_path):
    result = run(tmp_path, trials=12, verifier_cases=20, repetitions=3, seed=7, ablation_trials=4)

    assert result["seed"] == 7
    assert result["saga_fault_injection"]["trials"] == 12
    assert result["smt_policy_classification"]["cases"] == 20
    assert result["decay_properties"]["time_monotonicity_violations"] == 0
    assert result["filesystem_ablations"]["variants"]["full"]["safe_outcome_rate"] == 1.0
    assert result["control_failure_matrix"]["passed"] == result["control_failure_matrix"]["total"]
    assert (tmp_path / "saga_trials.csv").is_file()
    assert (tmp_path / "filesystem_ablation_trials.csv").is_file()
    persisted = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert persisted["synthetic_memory_clustering"]["repetitions"] == 3
