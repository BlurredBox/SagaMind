"""
SagaMind — Z3 Verifier Tests
==============================

Tests the Z3Verifier's semantic path-prefix checks, non-path argument
handling, numeric argument support, and empty-invariant edge cases.

Note: These tests exercise the *fallback* code path (z3-solver not
required at test time) which is the semantic validation branch.
"""

from unittest.mock import patch

import pytest

from src.config import settings
from src.verifier.z3_prover import Z3Verifier

# ─────────────────────────────────────────────────────────────────────
# Fixture — verifier in fallback mode (z3 not importable)
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def verifier():
    """Z3Verifier initialised in fallback mode (no z3 package)."""
    with patch.dict("sys.modules", {"z3": None}):
        v = Z3Verifier()
        assert v.z3_active is False
    return v


# ─────────────────────────────────────────────────────────────────────
# Path-Prefix Safety
# ─────────────────────────────────────────────────────────────────────


class TestPathPrefixVerification:
    """Validate the semantic guard for file-system path constraints."""

    def test_path_prefix_safe(self, verifier):
        """Path inside the authorised workspace root returns (True, ...)."""
        ok, msg = verifier.verify(
            {"path": f"{settings.allowed_workspace_root}/src/main.py"},
            f'(assert (str.prefixof "{settings.allowed_workspace_root}" path))',
        )
        assert ok is True

    def test_path_prefix_violation(self, verifier):
        """Path outside the workspace root returns (False, ...)."""
        ok, msg = verifier.verify(
            {"path": "/etc/passwd"},
            '(assert (str.prefixof "/Users/Harutyun/Desktop/Portfolio1" path))',
        )
        assert ok is False
        assert "outside authorized workspace" in msg.lower() or "Semantic Guard" in msg

    def test_path_traversal_attack(self, verifier):
        """Traversal attempt via ../../ is rejected."""
        ok, _ = verifier.verify(
            {"path": f"{settings.allowed_workspace_root}/../../etc/shadow"},
            f'(assert (str.prefixof "{settings.allowed_workspace_root}" path))',
        )
        assert ok is False

    def test_path_exact_root(self, verifier):
        """Exact root path is considered safe."""
        ok, _ = verifier.verify(
            {"path": settings.allowed_workspace_root},
            "",
        )
        assert ok is True


# ─────────────────────────────────────────────────────────────────────
# Non-path Arguments
# ─────────────────────────────────────────────────────────────────────


class TestNonPathArguments:
    """Arguments that do not contain a 'path' key should always pass."""

    def test_non_path_arguments_pass(self, verifier):
        ok, msg = verifier.verify(
            {"query": "SELECT 1", "timeout": 30},
            "",
        )
        assert ok is True
        assert "succeeded" in msg.lower()

    def test_empty_arguments_pass(self, verifier):
        ok, _ = verifier.verify({}, "")
        assert ok is True


# ─────────────────────────────────────────────────────────────────────
# Numeric Arguments
# ─────────────────────────────────────────────────────────────────────


class TestNumericArguments:
    """Numeric values must not cause type errors in the verifier."""

    def test_numeric_arguments(self, verifier):
        ok, _ = verifier.verify(
            {"retries": 3, "factor": 1.5, "enabled": True},
            "",
        )
        assert ok is True

    def test_mixed_argument_types(self, verifier):
        ok, _ = verifier.verify(
            {"name": "deploy", "count": 42, "force": False},
            "",
        )
        assert ok is True


# ─────────────────────────────────────────────────────────────────────
# Empty Invariants
# ─────────────────────────────────────────────────────────────────────


class TestEmptyInvariants:
    """Empty or whitespace-only invariant strings are handled gracefully."""

    def test_empty_invariants(self, verifier):
        ok, _ = verifier.verify({"path": f"{settings.allowed_workspace_root}/x"}, "")
        assert ok is True

    def test_whitespace_only_invariants(self, verifier):
        ok, _ = verifier.verify({"key": "val"}, "   ")
        assert ok is True

    def test_none_safe_path_with_empty_invariants(self, verifier):
        """Safe path + empty invariants → should pass."""
        ok, _ = verifier.verify(
            {"path": f"{settings.allowed_workspace_root}/data.json"},
            "",
        )
        assert ok is True
