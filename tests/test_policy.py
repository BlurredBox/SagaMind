"""Tests for typed mutating-tool policies."""

from src.policy import ArgumentRule, ToolPolicy, ToolPolicyRegistry, ValueKind, builtin_policy_registry
from src.verifier.z3_prover import VerificationStatus


def test_missing_policy_for_mutation_fails_closed():
    result = ToolPolicyRegistry().require("UNDECLARED", {"value": 1}, mutating=True)

    assert result.allowed is False
    assert result.violated_property == "missing_policy"
    assert result.counterexample == {"tool_name": "UNDECLARED", "arguments": {"value": 1}}


def test_read_only_action_without_policy_is_allowed():
    result = ToolPolicyRegistry().require("READ", {"key": "x"}, mutating=False)

    assert result.allowed is True


def test_nested_and_extra_values_fail_closed():
    registry = ToolPolicyRegistry()
    registry.register(ToolPolicy("MUTATE", True, {"count": ArgumentRule(ValueKind.INTEGER)}))

    nested = registry.require("MUTATE", {"count": {"nested": 1}}, mutating=True)
    extra = registry.require("MUTATE", {"count": 1, "surprise": True}, mutating=True)

    assert nested.status is VerificationStatus.REJECTED
    assert nested.violated_property == "argument.count.type"
    assert extra.violated_property == "arguments.additionalProperties"
    assert extra.repair_constraints == ({"field": "surprise", "constraint": "remove"},)


def test_numeric_bounds_return_repair_constraint():
    registry = ToolPolicyRegistry()
    registry.register(ToolPolicy("TRANSFER", True, {"amount": ArgumentRule(ValueKind.NUMBER, minimum=0, maximum=100)}))

    result = registry.require("TRANSFER", {"amount": 101}, mutating=True)

    assert result.allowed is False
    assert result.violated_property == "argument.amount.maximum"
    assert result.repair_constraints[0]["value"] == 100


def test_builtin_write_policy_accepts_contained_scalar_arguments(tmp_path, monkeypatch):
    monkeypatch.setattr("src.policy.contain_path", lambda value: value)
    registry = builtin_policy_registry()

    result = registry.require(
        "WRITE_FILE",
        {"path": str(tmp_path / "safe.txt"), "content": "safe"},
        mutating=True,
    )

    assert result.allowed is True


def test_builtin_restore_policy_requires_complete_typed_preimage(tmp_path, monkeypatch):
    monkeypatch.setattr("src.policy.contain_path", lambda value: value)
    registry = builtin_policy_registry()

    accepted = registry.require(
        "RESTORE_FILE",
        {"path": str(tmp_path / "safe.txt"), "existed": True, "previous": "before"},
        mutating=True,
    )
    malformed = registry.require(
        "RESTORE_FILE",
        {"path": str(tmp_path / "safe.txt"), "existed": "yes", "previous": "before"},
        mutating=True,
    )

    assert accepted.allowed is True
    assert malformed.violated_property == "argument.existed.type"


def test_policy_schema_rejects_empty_mutating_contract():
    try:
        ToolPolicy("EMPTY", True, {})
    except ValueError as exc:
        assert "non-empty" in str(exc)
    else:
        raise AssertionError("empty mutation policy was accepted")
