"""Bounded verifier and proof certificates for compensation contracts."""

from __future__ import annotations

import enum
import hashlib
import itertools
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from src.contracts.dsl import (
    AllOf,
    AnyOf,
    ArithmeticOperator,
    BinaryExpression,
    BoolConstant,
    Comparison,
    ComparisonOperator,
    CompensationContract,
    Condition,
    Expression,
    Literal,
    Not,
    Ref,
    RefSource,
    Scalar,
    Transition,
    ValueType,
    VariableSpec,
)


class ProofStatus(str, enum.Enum):
    PROVED = "proved"
    REJECTED = "rejected"
    INVALID = "invalid"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class RepairConstraint:
    """A machine-readable restriction suitable for an agent retry."""

    field: str
    source: str
    operator: str
    value: Scalar | dict[str, str]
    rationale: str


@dataclass(frozen=True)
class ContractCounterexample:
    violated_property: str
    original_state: dict[str, Scalar]
    inputs: dict[str, Scalar]
    after_forward: dict[str, Scalar]
    after_compensation: dict[str, Scalar] | None = None


@dataclass(frozen=True)
class VerificationCertificate:
    contract_name: str
    contract_version: str
    status: ProofStatus
    verifier: str
    contract_hash: str
    implementation_hash: str
    proof_hash: str
    cases_checked: int
    admissible_cases: int
    message: str
    counterexample: ContractCounterexample | None = None
    repair_constraints: tuple[RepairConstraint, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def proved(self) -> bool:
        return self.status is ProofStatus.PROVED

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


class ContractVerificationError(Exception):
    """Internal marker for an unsupported or invalid DSL construct."""


class UnsupportedContractError(ContractVerificationError):
    pass


class ProofSpaceTooLargeError(ContractVerificationError):
    pass


class BoundedContractVerifier:
    """Prove a contract by enumerating every member of its declared domains.

    The verifier deliberately fails closed.  Infinite domains, type errors,
    excessive state spaces and evaluation errors never produce a proof.
    """

    def __init__(self, max_cases: int = 100_000) -> None:
        if max_cases < 1:
            raise ValueError("max_cases must be positive")
        self.max_cases = max_cases

    def verify(self, contract: CompensationContract) -> VerificationCertificate:
        contract_hash = _sha256(contract.to_dict())
        try:
            self._validate_contract(contract)
            state_domains = self._domains(contract.state_variables, self.max_cases)
            input_domains = self._domains(contract.input_variables, self.max_cases)
        except ProofSpaceTooLargeError as exc:
            return self._certificate(contract, contract_hash, ProofStatus.UNKNOWN, f"{exc}; rejected.")
        except UnsupportedContractError as exc:
            return self._certificate(contract, contract_hash, ProofStatus.UNSUPPORTED, str(exc))
        except (ContractVerificationError, TypeError, ValueError) as exc:
            return self._certificate(contract, contract_hash, ProofStatus.INVALID, str(exc))

        case_count = _domain_size(state_domains) * _domain_size(input_domains)
        if case_count > self.max_cases:
            return self._certificate(
                contract,
                contract_hash,
                ProofStatus.UNKNOWN,
                f"Proof space has {case_count} cases, exceeding max_cases={self.max_cases}; rejected.",
            )

        checked = 0
        admissible = 0
        try:
            for original_state in _records(state_domains):
                for inputs in _records(input_domains):
                    checked += 1
                    if not self._condition(contract.precondition, original_state, original_state, inputs):
                        continue
                    admissible += 1
                    after_forward = self._transition(contract.forward, original_state, original_state, inputs)
                    self._ensure_state_types(after_forward, contract.state_variables)
                    if not self._condition(contract.postcondition, after_forward, original_state, inputs):
                        counterexample = ContractCounterexample(
                            violated_property="postcondition",
                            original_state=original_state,
                            inputs=inputs,
                            after_forward=after_forward,
                        )
                        return self._certificate(
                            contract,
                            contract_hash,
                            ProofStatus.REJECTED,
                            "Forward transition violates its declared postcondition.",
                            checked,
                            admissible,
                            counterexample,
                            self._repair_constraints(contract.postcondition, after_forward, original_state, inputs),
                        )

                    restored = self._transition(contract.compensation, after_forward, original_state, inputs)
                    self._ensure_state_types(restored, contract.state_variables)
                    if not self._condition(contract.restoration, restored, original_state, inputs):
                        counterexample = ContractCounterexample(
                            violated_property="restoration",
                            original_state=original_state,
                            inputs=inputs,
                            after_forward=after_forward,
                            after_compensation=restored,
                        )
                        return self._certificate(
                            contract,
                            contract_hash,
                            ProofStatus.REJECTED,
                            "Compensation does not restore the declared invariant.",
                            checked,
                            admissible,
                            counterexample,
                            self._repair_constraints(contract.restoration, restored, original_state, inputs),
                        )
        except UnsupportedContractError as exc:
            return self._certificate(contract, contract_hash, ProofStatus.UNSUPPORTED, str(exc), checked, admissible)
        except (ArithmeticError, ContractVerificationError, TypeError, ValueError) as exc:
            return self._certificate(
                contract,
                contract_hash,
                ProofStatus.INVALID,
                f"Contract evaluation failed: {exc}",
                checked,
                admissible,
            )

        if admissible == 0:
            return self._certificate(
                contract,
                contract_hash,
                ProofStatus.INVALID,
                "Precondition is unsatisfiable over the declared domains; vacuous proof rejected.",
                checked,
                admissible,
            )
        return self._certificate(
            contract,
            contract_hash,
            ProofStatus.PROVED,
            "Forward postcondition and compensation restoration hold for every admissible bounded case.",
            checked,
            admissible,
        )

    def _certificate(
        self,
        contract: CompensationContract,
        contract_hash: str,
        status: ProofStatus,
        message: str,
        cases_checked: int = 0,
        admissible_cases: int = 0,
        counterexample: ContractCounterexample | None = None,
        repair_constraints: tuple[RepairConstraint, ...] = (),
    ) -> VerificationCertificate:
        proof_payload = {
            "schema": "sagamind.compensation-proof.v1",
            "contract_hash": contract_hash,
            "implementation_hash": implementation_hash(contract.implementation_identity),
            "status": status.value,
            "verifier": "bounded-exhaustive-v1",
            "cases_checked": cases_checked,
            "admissible_cases": admissible_cases,
            "counterexample": asdict(counterexample) if counterexample else None,
            "repair_constraints": [asdict(item) for item in repair_constraints],
        }
        return VerificationCertificate(
            contract_name=contract.name,
            contract_version=contract.version,
            status=status,
            verifier="bounded-exhaustive-v1",
            contract_hash=contract_hash,
            implementation_hash=implementation_hash(contract.implementation_identity),
            proof_hash=_sha256(proof_payload),
            cases_checked=cases_checked,
            admissible_cases=admissible_cases,
            message=message,
            counterexample=counterexample,
            repair_constraints=repair_constraints,
        )

    @staticmethod
    def _domains(specs: tuple[VariableSpec, ...], max_points: int) -> dict[str, tuple[Scalar, ...]]:
        return {spec.name: _domain(spec, max_points) for spec in specs}

    @staticmethod
    def _validate_contract(contract: CompensationContract) -> None:
        state_specs = {item.name: item for item in contract.state_variables}
        input_specs = {item.name: item for item in contract.input_variables}
        if len(state_specs) != len(contract.state_variables):
            raise ContractVerificationError("State variable names must be unique")
        if len(input_specs) != len(contract.input_variables):
            raise ContractVerificationError("Input variable names must be unique")
        if set(state_specs) & set(input_specs):
            raise ContractVerificationError("State and input variable names must not overlap")
        if not state_specs:
            raise ContractVerificationError("At least one state variable is required")
        if not contract.implementation_identity.strip():
            raise ContractVerificationError("Tool implementation identity is required")
        if contract.irreversible_effects:
            effects = ", ".join(contract.irreversible_effects)
            raise UnsupportedContractError(f"Irreversible effects cannot receive a restoration proof: {effects}")

        for transition_name, transition in (
            ("forward", contract.forward),
            ("compensation", contract.compensation),
        ):
            targets = [assignment.target for assignment in transition.assignments]
            if len(set(targets)) != len(targets):
                raise ContractVerificationError(f"Duplicate {transition_name} assignment target")
            unknown = set(targets) - set(state_specs)
            if unknown:
                raise ContractVerificationError(f"Unknown {transition_name} targets: {sorted(unknown)}")

        for condition in (contract.precondition, contract.postcondition, contract.restoration):
            _validate_refs(condition, state_specs, input_specs)
            _validate_condition_types(condition, state_specs, input_specs)
        for transition in (contract.forward, contract.compensation):
            for assignment in transition.assignments:
                _validate_refs(assignment.value, state_specs, input_specs)
                assigned_type = _infer_expression_type(assignment.value, state_specs, input_specs)
                target_type = state_specs[assignment.target].value_type
                if not _assignable_type(assigned_type, target_type):
                    raise ContractVerificationError(
                        f"Assignment to {assignment.target!r} produces {assigned_type.value}, "
                        f"expected {target_type.value}"
                    )

    @staticmethod
    def _ensure_state_types(state: dict[str, Scalar], specs: tuple[VariableSpec, ...]) -> None:
        for spec in specs:
            if not _matches_type(state[spec.name], spec.value_type):
                raise ContractVerificationError(
                    f"Transition produced {state[spec.name]!r} for {spec.name!r}, expected {spec.value_type.value}"
                )

    def _transition(
        self,
        transition: Transition,
        state: dict[str, Scalar],
        original: dict[str, Scalar],
        inputs: dict[str, Scalar],
    ) -> dict[str, Scalar]:
        updates = {
            assignment.target: self._expression(assignment.value, state, original, inputs)
            for assignment in transition.assignments
        }
        result = dict(state)
        result.update(updates)
        return result

    def _condition(
        self,
        condition: Condition,
        state: dict[str, Scalar],
        original: dict[str, Scalar],
        inputs: dict[str, Scalar],
    ) -> bool:
        if isinstance(condition, BoolConstant):
            return condition.value
        if isinstance(condition, AllOf):
            return all(self._condition(item, state, original, inputs) for item in condition.conditions)
        if isinstance(condition, AnyOf):
            return any(self._condition(item, state, original, inputs) for item in condition.conditions)
        if isinstance(condition, Not):
            return not self._condition(condition.condition, state, original, inputs)
        if isinstance(condition, Comparison):
            left = self._expression(condition.left, state, original, inputs)
            right = self._expression(condition.right, state, original, inputs)
            return _compare(condition.operator, left, right)
        raise UnsupportedContractError(f"Unsupported condition: {type(condition).__name__}")

    def _expression(
        self,
        expression: Expression,
        state: dict[str, Scalar],
        original: dict[str, Scalar],
        inputs: dict[str, Scalar],
    ) -> Scalar:
        if isinstance(expression, Literal):
            return expression.value
        if isinstance(expression, Ref):
            source = {
                RefSource.STATE: state,
                RefSource.ORIGINAL: original,
                RefSource.INPUT: inputs,
            }[expression.source]
            return source[expression.name]
        if isinstance(expression, BinaryExpression):
            left = self._expression(expression.left, state, original, inputs)
            right = self._expression(expression.right, state, original, inputs)
            if isinstance(left, (bool, str)) or isinstance(right, (bool, str)):
                raise ContractVerificationError("Arithmetic operands must be numeric")
            if expression.operator is ArithmeticOperator.ADD:
                return left + right
            if expression.operator is ArithmeticOperator.SUBTRACT:
                return left - right
            if expression.operator is ArithmeticOperator.MULTIPLY:
                return left * right
        raise UnsupportedContractError(f"Unsupported expression: {type(expression).__name__}")

    def _repair_constraints(
        self,
        condition: Condition,
        state: dict[str, Scalar],
        original: dict[str, Scalar],
        inputs: dict[str, Scalar],
    ) -> tuple[RepairConstraint, ...]:
        failed = _failed_comparisons(condition, self, state, original, inputs)
        return tuple(_comparison_to_repair(item) for item in failed if isinstance(item.left, Ref))


def _domain(spec: VariableSpec, max_points: int) -> tuple[Scalar, ...]:
    values: tuple[Scalar, ...]
    if spec.allowed_values:
        values = spec.allowed_values
    elif spec.value_type is ValueType.BOOLEAN:
        values = (False, True)
    elif spec.value_type is ValueType.INTEGER and spec.minimum is not None and spec.maximum is not None:
        if spec.maximum - spec.minimum + 1 > max_points:
            raise ProofSpaceTooLargeError(f"Domain for {spec.name!r} exceeds max_cases={max_points}")
        values = tuple(range(spec.minimum, spec.maximum + 1))
    else:
        raise UnsupportedContractError(
            f"{spec.name!r} has no finite domain; real/string variables require allowed_values "
            "and integers require inclusive bounds"
        )
    if not values:
        raise ContractVerificationError(f"{spec.name!r} has an empty domain")
    if len(values) > max_points:
        raise ProofSpaceTooLargeError(f"Domain for {spec.name!r} exceeds max_cases={max_points}")
    for value in values:
        if not _matches_type(value, spec.value_type):
            raise ContractVerificationError(f"Value {value!r} in {spec.name!r} does not match {spec.value_type.value}")
    return values


def _matches_type(value: Scalar, value_type: ValueType) -> bool:
    if value_type is ValueType.BOOLEAN:
        return isinstance(value, bool)
    if value_type is ValueType.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type is ValueType.REAL:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if value_type is ValueType.STRING:
        return isinstance(value, str)
    return False


def _records(domains: dict[str, tuple[Scalar, ...]]) -> Iterable[dict[str, Scalar]]:
    if not domains:
        yield {}
        return
    names = tuple(domains)
    for values in itertools.product(*(domains[name] for name in names)):
        yield dict(zip(names, values, strict=True))


def _domain_size(domains: dict[str, tuple[Scalar, ...]]) -> int:
    result = 1
    for values in domains.values():
        result *= len(values)
    return result


def _compare(operator: ComparisonOperator, left: Scalar, right: Scalar) -> bool:
    if operator is ComparisonOperator.EQ:
        return left == right
    if operator is ComparisonOperator.NE:
        return left != right
    if operator is ComparisonOperator.LT:
        return left < right  # type: ignore[operator]
    if operator is ComparisonOperator.LE:
        return left <= right  # type: ignore[operator]
    if operator is ComparisonOperator.GT:
        return left > right  # type: ignore[operator]
    if operator is ComparisonOperator.GE:
        return left >= right  # type: ignore[operator]
    raise UnsupportedContractError(f"Unsupported comparison operator: {operator}")


def _validate_refs(
    node: Condition | Expression,
    state_specs: dict[str, VariableSpec],
    input_specs: dict[str, VariableSpec],
) -> None:
    if isinstance(node, Ref):
        known = input_specs if node.source is RefSource.INPUT else state_specs
        if node.name not in known:
            raise ContractVerificationError(f"Unknown {node.source.value} reference: {node.name}")
        return
    if isinstance(node, (Literal, BoolConstant)):
        return
    if isinstance(node, (BinaryExpression, Comparison)):
        _validate_refs(node.left, state_specs, input_specs)
        _validate_refs(node.right, state_specs, input_specs)
        return
    if isinstance(node, (AllOf, AnyOf)):
        for item in node.conditions:
            _validate_refs(item, state_specs, input_specs)
        return
    if isinstance(node, Not):
        _validate_refs(node.condition, state_specs, input_specs)
        return
    raise UnsupportedContractError(f"Unsupported DSL node: {type(node).__name__}")


def _validate_condition_types(
    condition: Condition,
    state_specs: dict[str, VariableSpec],
    input_specs: dict[str, VariableSpec],
) -> None:
    if isinstance(condition, BoolConstant):
        return
    if isinstance(condition, Comparison):
        left = _infer_expression_type(condition.left, state_specs, input_specs)
        right = _infer_expression_type(condition.right, state_specs, input_specs)
        if not _compatible_types(left, right):
            raise ContractVerificationError(f"Comparison mixes incompatible types: {left.value} and {right.value}")
        return
    if isinstance(condition, (AllOf, AnyOf)):
        for item in condition.conditions:
            _validate_condition_types(item, state_specs, input_specs)
        return
    if isinstance(condition, Not):
        _validate_condition_types(condition.condition, state_specs, input_specs)
        return
    raise UnsupportedContractError(f"Unsupported condition: {type(condition).__name__}")


def _infer_expression_type(
    expression: Expression,
    state_specs: dict[str, VariableSpec],
    input_specs: dict[str, VariableSpec],
) -> ValueType:
    if isinstance(expression, Literal):
        if isinstance(expression.value, bool):
            return ValueType.BOOLEAN
        if isinstance(expression.value, int):
            return ValueType.INTEGER
        if isinstance(expression.value, float):
            return ValueType.REAL
        if isinstance(expression.value, str):
            return ValueType.STRING
    if isinstance(expression, Ref):
        specs = input_specs if expression.source is RefSource.INPUT else state_specs
        return specs[expression.name].value_type
    if isinstance(expression, BinaryExpression):
        left = _infer_expression_type(expression.left, state_specs, input_specs)
        right = _infer_expression_type(expression.right, state_specs, input_specs)
        numeric = {ValueType.INTEGER, ValueType.REAL}
        if left not in numeric or right not in numeric:
            raise ContractVerificationError("Arithmetic expressions require numeric operands")
        return ValueType.REAL if ValueType.REAL in (left, right) else ValueType.INTEGER
    raise UnsupportedContractError(f"Unsupported expression: {type(expression).__name__}")


def _compatible_types(left: ValueType, right: ValueType) -> bool:
    if left is right:
        return True
    return {left, right} == {ValueType.INTEGER, ValueType.REAL}


def _assignable_type(value_type: ValueType, target_type: ValueType) -> bool:
    return value_type is target_type or (value_type is ValueType.INTEGER and target_type is ValueType.REAL)


def _failed_comparisons(
    condition: Condition,
    verifier: BoundedContractVerifier,
    state: dict[str, Scalar],
    original: dict[str, Scalar],
    inputs: dict[str, Scalar],
) -> list[Comparison]:
    if isinstance(condition, Comparison):
        return [] if verifier._condition(condition, state, original, inputs) else [condition]
    if isinstance(condition, AllOf):
        return [
            comparison
            for item in condition.conditions
            for comparison in _failed_comparisons(item, verifier, state, original, inputs)
        ]
    if isinstance(condition, AnyOf):
        if verifier._condition(condition, state, original, inputs):
            return []
        return [
            comparison
            for item in condition.conditions
            for comparison in _failed_comparisons(item, verifier, state, original, inputs)
        ]
    return []


def _comparison_to_repair(comparison: Comparison) -> RepairConstraint:
    assert isinstance(comparison.left, Ref)
    right: Scalar | dict[str, str]
    if isinstance(comparison.right, Literal):
        right = comparison.right.value
    elif isinstance(comparison.right, Ref):
        right = {"source": comparison.right.source.value, "field": comparison.right.name}
    else:
        right = {"expression": repr(comparison.right)}
    return RepairConstraint(
        field=comparison.left.name,
        source=comparison.left.source.value,
        operator=comparison.operator.value,
        value=right,
        rationale="Retry must satisfy the violated contract predicate.",
    )


def _sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def implementation_hash(identity: str) -> str:
    """Hash a stable tool implementation identity for certificate binding."""
    return _sha256({"tool_implementation_identity": identity})
