"""Executable specifications for verified compensation contracts."""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.contracts import (
    AllOf,
    AnyOf,
    ArithmeticOperator,
    Assignment,
    BinaryExpression,
    BoolConstant,
    BoundedContractVerifier,
    Comparison,
    ComparisonOperator,
    CompensationContract,
    Not,
    ProofStatus,
    Transition,
    ValueType,
    VariableSpec,
    input_value,
    literal,
    original,
    state,
)


def withdrawal_contract() -> CompensationContract:
    return CompensationContract(
        name="bounded-withdrawal",
        version="1.0.0",
        state_variables=(VariableSpec("balance", ValueType.INTEGER, minimum=0, maximum=3),),
        input_variables=(VariableSpec("amount", ValueType.INTEGER, minimum=0, maximum=2),),
        precondition=AllOf(
            (
                Comparison(ComparisonOperator.GE, input_value("amount"), literal(0)),
                Comparison(ComparisonOperator.LE, input_value("amount"), state("balance")),
            )
        ),
        forward=Transition(
            (
                Assignment(
                    "balance",
                    BinaryExpression(ArithmeticOperator.SUBTRACT, state("balance"), input_value("amount")),
                ),
            )
        ),
        postcondition=Comparison(ComparisonOperator.GE, state("balance"), literal(0)),
        compensation=Transition(
            (
                Assignment(
                    "balance",
                    BinaryExpression(ArithmeticOperator.ADD, state("balance"), input_value("amount")),
                ),
            )
        ),
        restoration=Comparison(ComparisonOperator.EQ, state("balance"), original("balance")),
        implementation_identity="tests.withdrawal:v1:forward+compensation",
    )


def test_proves_compensation_for_every_admissible_state() -> None:
    certificate = BoundedContractVerifier().verify(withdrawal_contract())

    assert certificate.status is ProofStatus.PROVED
    assert certificate.proved is True
    assert certificate.cases_checked == 12
    assert certificate.admissible_cases == 9
    assert certificate.contract_hash.startswith("sha256:")
    assert certificate.implementation_hash.startswith("sha256:")
    assert certificate.proof_hash.startswith("sha256:")
    assert certificate.to_dict()["status"] == "proved"


def test_certificate_hash_is_deterministic_and_contract_bound() -> None:
    verifier = BoundedContractVerifier()
    first = verifier.verify(withdrawal_contract())
    second = verifier.verify(withdrawal_contract())
    changed = verifier.verify(replace(withdrawal_contract(), version="1.0.1"))
    changed_implementation = verifier.verify(
        replace(withdrawal_contract(), implementation_identity="tests.withdrawal:v2")
    )

    assert first.proof_hash == second.proof_hash
    assert first.contract_hash == second.contract_hash
    assert changed.contract_hash != first.contract_hash
    assert changed_implementation.implementation_hash != first.implementation_hash
    assert changed_implementation.proof_hash != first.proof_hash


def test_broken_compensation_returns_counterexample_and_repair() -> None:
    contract = withdrawal_contract()
    broken = replace(
        contract,
        compensation=Transition(
            (
                Assignment(
                    "balance",
                    BinaryExpression(ArithmeticOperator.SUBTRACT, state("balance"), input_value("amount")),
                ),
            )
        ),
    )

    certificate = BoundedContractVerifier().verify(broken)

    assert certificate.status is ProofStatus.REJECTED
    assert certificate.counterexample is not None
    assert certificate.counterexample.violated_property == "restoration"
    assert certificate.counterexample.after_compensation != certificate.counterexample.original_state
    assert certificate.repair_constraints[0].field == "balance"
    assert certificate.repair_constraints[0].operator == "eq"
    assert certificate.repair_constraints[0].value == {"source": "original", "field": "balance"}


def test_forward_postcondition_violation_is_not_mislabeled_as_proof() -> None:
    contract = replace(
        withdrawal_contract(),
        postcondition=Comparison(ComparisonOperator.EQ, state("balance"), literal(99)),
    )

    certificate = BoundedContractVerifier().verify(contract)

    assert certificate.status is ProofStatus.REJECTED
    assert certificate.counterexample is not None
    assert certificate.counterexample.violated_property == "postcondition"
    assert certificate.repair_constraints[0].value == 99


def test_unbounded_domain_is_explicitly_unsupported_and_fails_closed() -> None:
    contract = replace(
        withdrawal_contract(),
        input_variables=(VariableSpec("amount", ValueType.REAL),),
        precondition=BoolConstant(True),
        forward=Transition(),
        postcondition=BoolConstant(True),
        compensation=Transition(),
    )

    certificate = BoundedContractVerifier().verify(contract)

    assert certificate.status is ProofStatus.UNSUPPORTED
    assert certificate.proved is False
    assert "finite domain" in certificate.message


def test_oversized_proof_is_unknown_and_fails_closed() -> None:
    certificate = BoundedContractVerifier(max_cases=2).verify(withdrawal_contract())

    assert certificate.status is ProofStatus.UNKNOWN
    assert certificate.proved is False
    assert "exceed" in certificate.message


def test_vacuous_precondition_is_invalid() -> None:
    contract = replace(withdrawal_contract(), precondition=BoolConstant(False))

    certificate = BoundedContractVerifier().verify(contract)

    assert certificate.status is ProofStatus.INVALID
    assert "vacuous" in certificate.message


def test_irreversible_effect_cannot_receive_restoration_certificate() -> None:
    contract = replace(withdrawal_contract(), irreversible_effects=("email_sent",))

    certificate = BoundedContractVerifier().verify(contract)

    assert certificate.status is ProofStatus.UNSUPPORTED
    assert "Irreversible" in certificate.message


def test_unknown_reference_is_invalid() -> None:
    contract = replace(
        withdrawal_contract(),
        restoration=Comparison(ComparisonOperator.EQ, state("missing"), original("balance")),
    )

    certificate = BoundedContractVerifier().verify(contract)

    assert certificate.status is ProofStatus.INVALID
    assert "Unknown state reference" in certificate.message


def test_contract_and_domain_schema_validation() -> None:
    with pytest.raises(ValueError, match="positive"):
        BoundedContractVerifier(max_cases=0)
    with pytest.raises(ValueError, match="Invalid variable"):
        VariableSpec("not valid", ValueType.INTEGER, minimum=0, maximum=1)
    with pytest.raises(ValueError, match="supplied together"):
        VariableSpec("x", ValueType.INTEGER, minimum=0)
    with pytest.raises(ValueError, match="exceed"):
        VariableSpec("x", ValueType.INTEGER, minimum=2, maximum=1)
    with pytest.raises(ValueError, match="cannot be empty"):
        replace(withdrawal_contract(), name=" ")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"state_variables": (VariableSpec("x", ValueType.BOOLEAN),) * 2}, "State variable names"),
        ({"input_variables": (VariableSpec("x", ValueType.BOOLEAN),) * 2}, "Input variable names"),
        (
            {
                "state_variables": (VariableSpec("same", ValueType.BOOLEAN),),
                "input_variables": (VariableSpec("same", ValueType.BOOLEAN),),
            },
            "must not overlap",
        ),
        ({"state_variables": ()}, "At least one"),
        (
            {"forward": Transition((Assignment("balance", literal(1)), Assignment("balance", literal(2))))},
            "Duplicate forward",
        ),
        ({"forward": Transition((Assignment("missing", literal(1)),))}, "Unknown forward targets"),
        ({"forward": Transition((Assignment("balance", literal("bad")),))}, "expected integer"),
    ],
)
def test_invalid_contract_shapes_fail_closed(change, message) -> None:
    certificate = BoundedContractVerifier().verify(replace(withdrawal_contract(), **change))

    assert certificate.status is ProofStatus.INVALID
    assert message in certificate.message


def test_boolean_string_real_and_logical_dsl_paths() -> None:
    contract = CompensationContract(
        name="typed-logic",
        state_variables=(
            VariableSpec("enabled", ValueType.BOOLEAN),
            VariableSpec("label", ValueType.STRING, allowed_values=("a", "b")),
            VariableSpec("score", ValueType.REAL, allowed_values=(0.5, 1.0)),
        ),
        input_variables=(),
        precondition=AnyOf(
            (
                Comparison(ComparisonOperator.EQ, state("label"), literal("a")),
                Not(Comparison(ComparisonOperator.LT, state("score"), literal(1.0))),
            )
        ),
        forward=Transition(
            (
                Assignment("enabled", literal(True)),
                Assignment(
                    "score",
                    BinaryExpression(ArithmeticOperator.MULTIPLY, state("score"), literal(1.0)),
                ),
            )
        ),
        postcondition=AllOf(
            (
                Comparison(ComparisonOperator.NE, state("label"), literal("missing")),
                Comparison(ComparisonOperator.GT, state("score"), literal(0.0)),
                Comparison(ComparisonOperator.GE, state("score"), literal(0.5)),
            )
        ),
        compensation=Transition(
            (
                Assignment("enabled", original("enabled")),
                Assignment("score", original("score")),
            )
        ),
        restoration=AllOf(
            (
                Comparison(ComparisonOperator.EQ, state("enabled"), original("enabled")),
                Comparison(ComparisonOperator.EQ, state("score"), original("score")),
            )
        ),
        implementation_identity="tests.typed_logic:v1",
    )

    certificate = BoundedContractVerifier().verify(contract)

    assert certificate.status is ProofStatus.PROVED


def test_wrong_finite_domain_value_type_is_invalid() -> None:
    contract = replace(
        withdrawal_contract(),
        input_variables=(VariableSpec("amount", ValueType.INTEGER, allowed_values=(True,)),),
    )

    certificate = BoundedContractVerifier().verify(contract)

    assert certificate.status is ProofStatus.INVALID
    assert "does not match integer" in certificate.message
