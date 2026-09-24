"""Typed, fail-closed policies for tool arguments.

The schema handles the common policy boundary without asking callers to build
SMT-LIB strings. ``advanced_smt`` is an explicit escape hatch and is evaluated
only after the typed checks succeed.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from src.security import PathSecurityError, contain_path
from src.verifier.z3_prover import PolicyVerificationResult, VerificationStatus, Z3Verifier


class ValueKind(str, enum.Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    PATH = "path"


@dataclass(frozen=True)
class ArgumentRule:
    """Declarative constraints for one scalar tool argument."""

    kind: ValueKind
    required: bool = True
    enum_values: tuple[str | int | float | bool, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    max_length: int | None = None

    def __post_init__(self) -> None:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum cannot exceed maximum")
        if self.max_length is not None and self.max_length < 0:
            raise ValueError("max_length cannot be negative")
        if self.max_length is not None and self.kind not in {ValueKind.STRING, ValueKind.PATH}:
            raise ValueError("max_length is valid only for string and path rules")
        if (self.minimum is not None or self.maximum is not None) and self.kind not in {
            ValueKind.INTEGER,
            ValueKind.NUMBER,
        }:
            raise ValueError("numeric bounds are valid only for integer and number rules")


@dataclass(frozen=True)
class ToolPolicy:
    """Complete policy for one tool.

    Mutating policies fail closed on missing and extra arguments by default.
    Nested values have no implicit abstraction and are rejected.
    """

    tool_name: str
    mutating: bool
    arguments: dict[str, ArgumentRule] = field(default_factory=dict)
    allow_extra_arguments: bool = False
    advanced_smt: str | None = None

    def __post_init__(self) -> None:
        if not self.tool_name:
            raise ValueError("tool_name is required")
        if self.mutating and not self.arguments:
            raise ValueError("mutating tools require a non-empty typed argument policy")


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    status: VerificationStatus
    explanation: str
    violated_property: str | None = None
    counterexample: dict[str, Any] | None = None
    repair_constraints: tuple[dict[str, Any], ...] = ()


class ToolPolicyRegistry:
    """Registry and evaluator for typed tool policies."""

    def __init__(self, verifier: Z3Verifier | None = None) -> None:
        self._policies: dict[str, ToolPolicy] = {}
        self._verifier = verifier

    def register(self, policy: ToolPolicy) -> None:
        if policy.tool_name in self._policies:
            raise ValueError(f"Policy already registered for {policy.tool_name!r}")
        self._policies[policy.tool_name] = policy

    def get(self, tool_name: str) -> ToolPolicy | None:
        return self._policies.get(tool_name)

    def require(self, tool_name: str, arguments: dict[str, Any], *, mutating: bool) -> PolicyDecision:
        policy = self._policies.get(tool_name)
        if policy is None:
            if mutating:
                return _rejection("missing_policy", tool_name, arguments, "Mutating tool has no registered policy.")
            return PolicyDecision(True, VerificationStatus.SAFE, "Read-only tool needs no mutation policy.")
        if mutating and not policy.mutating:
            return _rejection(
                "mutation_classification",
                tool_name,
                arguments,
                "Tool is mutating but its policy is marked read-only.",
            )
        decision = self._evaluate_typed(policy, arguments)
        if not decision.allowed or not policy.advanced_smt:
            return decision
        verifier = self._verifier or Z3Verifier()
        detailed: PolicyVerificationResult = verifier.verify_detailed(arguments, policy.advanced_smt)
        return PolicyDecision(
            detailed.allowed,
            detailed.status,
            detailed.explanation,
            detailed.violated_property,
            detailed.counterexample,
            detailed.repair_constraints,
        )

    @staticmethod
    def _evaluate_typed(policy: ToolPolicy, arguments: dict[str, Any]) -> PolicyDecision:
        for name, rule in policy.arguments.items():
            if name not in arguments:
                if rule.required:
                    return _rejection(
                        f"argument.{name}.required",
                        policy.tool_name,
                        arguments,
                        f"Required argument {name!r} is missing.",
                        ({"field": name, "constraint": "required", "value": True},),
                    )
                continue
            value = arguments[name]
            if not _has_kind(value, rule.kind):
                return _rejection(
                    f"argument.{name}.type",
                    policy.tool_name,
                    arguments,
                    f"Argument {name!r} must be {rule.kind.value}.",
                    ({"field": name, "constraint": "type", "value": rule.kind.value},),
                )
            if rule.kind is ValueKind.PATH:
                try:
                    contain_path(value)
                except (PathSecurityError, TypeError) as exc:
                    return _rejection(f"argument.{name}.containment", policy.tool_name, arguments, str(exc))
            if rule.enum_values and value not in rule.enum_values:
                return _rejection(
                    f"argument.{name}.enum",
                    policy.tool_name,
                    arguments,
                    f"Argument {name!r} is outside its allowed set.",
                    ({"field": name, "constraint": "enum", "value": list(rule.enum_values)},),
                )
            if rule.minimum is not None and value < rule.minimum:
                return _rejection(
                    f"argument.{name}.minimum",
                    policy.tool_name,
                    arguments,
                    f"Argument {name!r} is below its minimum.",
                    ({"field": name, "constraint": "minimum", "value": rule.minimum},),
                )
            if rule.maximum is not None and value > rule.maximum:
                return _rejection(
                    f"argument.{name}.maximum",
                    policy.tool_name,
                    arguments,
                    f"Argument {name!r} exceeds its maximum.",
                    ({"field": name, "constraint": "maximum", "value": rule.maximum},),
                )
            if rule.max_length is not None and len(value) > rule.max_length:
                return _rejection(
                    f"argument.{name}.max_length",
                    policy.tool_name,
                    arguments,
                    f"Argument {name!r} exceeds its maximum length.",
                    ({"field": name, "constraint": "maxLength", "value": rule.max_length},),
                )
        extra = sorted(set(arguments) - set(policy.arguments))
        if extra and not policy.allow_extra_arguments:
            return _rejection(
                "arguments.additionalProperties",
                policy.tool_name,
                arguments,
                f"Unexpected arguments rejected: {', '.join(extra)}.",
                tuple({"field": name, "constraint": "remove"} for name in extra),
            )
        return PolicyDecision(True, VerificationStatus.SAFE, "Typed tool policy satisfied.")


def _has_kind(value: Any, kind: ValueKind) -> bool:
    if kind in {ValueKind.STRING, ValueKind.PATH}:
        return isinstance(value, str)
    if kind is ValueKind.BOOLEAN:
        return isinstance(value, bool)
    if kind is ValueKind.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if kind is ValueKind.NUMBER:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def _rejection(
    property_name: str,
    tool_name: str,
    arguments: dict[str, Any],
    explanation: str,
    repair: tuple[dict[str, Any], ...] = (),
) -> PolicyDecision:
    return PolicyDecision(
        False,
        VerificationStatus.REJECTED,
        explanation,
        violated_property=property_name,
        counterexample={"tool_name": tool_name, "arguments": arguments},
        repair_constraints=repair,
    )


def builtin_policy_registry(verifier: Z3Verifier | None = None) -> ToolPolicyRegistry:
    """Return the policies shipped for SagaMind's built-in mutating tools."""
    policies = ToolPolicyRegistry(verifier)
    policies.register(
        ToolPolicy(
            "WRITE_FILE",
            mutating=True,
            arguments={
                "path": ArgumentRule(ValueKind.PATH),
                "content": ArgumentRule(ValueKind.STRING, max_length=1_000_000),
                "expected_sha256": ArgumentRule(ValueKind.STRING, required=False, max_length=64),
                "expected_absent": ArgumentRule(ValueKind.BOOLEAN, required=False),
            },
        )
    )
    policies.register(
        ToolPolicy(
            "DELETE_FILE",
            mutating=True,
            arguments={
                "path": ArgumentRule(ValueKind.PATH),
                "expected_sha256": ArgumentRule(ValueKind.STRING, required=False, max_length=64),
                "expected_absent": ArgumentRule(ValueKind.BOOLEAN, required=False),
            },
        )
    )
    policies.register(
        ToolPolicy(
            "RESTORE_FILE",
            mutating=True,
            arguments={
                "path": ArgumentRule(ValueKind.PATH),
                "existed": ArgumentRule(ValueKind.BOOLEAN),
                "previous": ArgumentRule(ValueKind.STRING, max_length=1_000_000),
                "expected_sha256": ArgumentRule(ValueKind.STRING, required=False, max_length=64),
                "expected_absent": ArgumentRule(ValueKind.BOOLEAN, required=False),
            },
        )
    )
    return policies
