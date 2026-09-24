"""Structured counterexamples and repair constraints for policy rejection."""

from __future__ import annotations

from unittest.mock import patch

from src.verifier.z3_prover import VerificationStatus, Z3Verifier, _derive_repair_constraints


def test_derives_numeric_and_field_relative_repair_constraints() -> None:
    policy = "(assert (and (>= amount 0) (<= amount account_balance)))"

    repairs = _derive_repair_constraints(policy, {"amount": 1500, "account_balance": 900})

    assert repairs == [
        {
            "field": "amount",
            "constraint": "maximum",
            "value": {"field": "account_balance"},
            "predicate": "amount <= account_balance",
        }
    ]


def test_solver_rejection_returns_counterexample_guided_repair() -> None:
    verifier = Z3Verifier()

    result = verifier.verify_detailed(
        {"amount": 1500, "account_balance": 900},
        "(assert (>= amount 0)) (assert (<= amount account_balance))",
    )

    assert result.allowed is False
    assert result.status is VerificationStatus.REJECTED
    assert result.counterexample == {"amount": 1500, "account_balance": 900}
    assert result.repair_constraints == (
        {
            "field": "amount",
            "constraint": "maximum",
            "value": {"field": "account_balance"},
            "predicate": "amount <= account_balance",
        },
    )


def test_detailed_verification_rejects_unsupported_arguments() -> None:
    verifier = Z3Verifier()

    result = verifier.verify_detailed({"headers": {"x": "y"}}, "(assert true)")

    assert result.allowed is False
    assert result.status is VerificationStatus.UNSUPPORTED
    assert result.to_dict()["status"] == "unsupported"


def test_detailed_verification_fails_closed_without_solver() -> None:
    with patch.dict("sys.modules", {"z3": None}):
        verifier = Z3Verifier()

    result = verifier.verify_detailed({"amount": 5}, "(assert (<= amount 3))")

    assert result.allowed is False
    assert result.status is VerificationStatus.UNKNOWN
    assert result.violated_property == "(assert (<= amount 3))"


def test_empty_policy_preserves_semantic_guard() -> None:
    with patch.dict("sys.modules", {"z3": None}):
        verifier = Z3Verifier()

    result = verifier.verify_detailed({"query": "SELECT 1"}, "")

    assert result.allowed is True
    assert result.status is VerificationStatus.SAFE
