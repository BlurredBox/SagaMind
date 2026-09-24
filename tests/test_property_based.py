"""
Property-based tests using Hypothesis.

The entire module is skipped when hypothesis is not installed (pytest.importorskip
handles it at collection time before any decorator is evaluated).
"""

from __future__ import annotations

import math
import os
import tempfile
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

hypothesis = pytest.importorskip("hypothesis", reason="hypothesis not installed")

from hypothesis import given  # noqa: E402
from hypothesis import settings as h_settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from src.models import ActionPayload, SagaStatus, SagaStep, StepStatus  # noqa: E402
from src.orchestrator.coordinator import SagaTransactionCoordinator  # noqa: E402

# ── Helpers ───────────────────────────────────────────────────────────────────

_TERMINAL_STATUSES = {
    SagaStatus.COMMITTED.value,
    SagaStatus.ROLLED_BACK.value,
    SagaStatus.COMPENSATION_FAILED.value,
}


def _make_step(name: str) -> SagaStep:
    return SagaStep(
        step_id=str(uuid.uuid4()),
        step_name=name,
        action=ActionPayload("NOOP", {}),
        compensation=ActionPayload("NOOP", {}),
        invariants="",
    )


def _make_coordinator(steps_fail: list[bool], comp_fails: list[bool]) -> SagaTransactionCoordinator:
    """Build a coordinator whose sandbox fails forward/compensation steps per the booleans."""
    verifier = MagicMock()
    verifier.verify.return_value = (True, "")

    sandbox = MagicMock()
    execute_calls: list[int] = [0]
    comp_calls: list[int] = [0]

    def _execute(_action: Any) -> Any:
        idx = execute_calls[0]
        execute_calls[0] += 1
        if idx < len(steps_fail) and steps_fail[idx]:
            raise RuntimeError("injected step failure")
        from src.models import SandboxResult

        return SandboxResult(success=True)

    def _comp(_action: Any) -> bool:
        idx = comp_calls[0]
        comp_calls[0] += 1
        return not (idx < len(comp_fails) and comp_fails[idx])

    sandbox.execute.side_effect = _execute
    sandbox.execute_compensation.side_effect = _comp
    return SagaTransactionCoordinator(verifier, sandbox)


# ── Saga FSM properties ───────────────────────────────────────────────────────


@given(
    step_count=st.integers(min_value=0, max_value=6),
    fail_mask=st.integers(min_value=0, max_value=63),
    comp_mask=st.integers(min_value=0, max_value=63),
)
@h_settings(max_examples=200, deadline=2000)
def test_saga_always_reaches_terminal_state(step_count: int, fail_mask: int, comp_mask: int) -> None:
    """For any combination of step/compensation failures the saga must end in a terminal state."""
    steps_fail = [(fail_mask >> i) & 1 == 1 for i in range(step_count)]
    comp_fails = [(comp_mask >> i) & 1 == 1 for i in range(step_count)]

    coord = _make_coordinator(steps_fail, comp_fails)
    saga_id = str(uuid.uuid4())
    coord.start_transaction_log(saga_id, "property-test goal", "test-tenant")
    steps = [_make_step(f"step-{i}") for i in range(step_count)]

    try:
        coord.execute_saga(saga_id, steps)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"execute_saga raised unexpectedly: {exc}")

    saga = coord.active_sagas.get(saga_id)
    if saga is None:
        return  # evicted — acceptable
    assert saga.status in _TERMINAL_STATUSES, (
        f"saga ended in non-terminal status '{saga.status}' "
        f"(n={step_count}, fail={fail_mask:#06b}, comp={comp_mask:#06b})"
    )


@given(n=st.integers(min_value=1, max_value=8))
@h_settings(max_examples=100, deadline=2000)
def test_fully_committed_saga_all_steps_committed(n: int) -> None:
    """When no step fails, every step status must be COMMITTED after execute_saga."""
    coord = _make_coordinator([False] * n, [])
    saga_id = str(uuid.uuid4())
    coord.start_transaction_log(saga_id, "all-commit", "t1")
    steps = [_make_step(f"s{i}") for i in range(n)]
    coord.execute_saga(saga_id, steps)
    saga = coord.active_sagas.get(saga_id)
    if saga and saga.status == SagaStatus.COMMITTED.value:
        for s in saga.completed_steps:
            assert s.status == StepStatus.COMMITTED.value


# ── Decay math properties ─────────────────────────────────────────────────────


@given(
    s_init=st.floats(min_value=0.001, max_value=1000.0, allow_nan=False, allow_infinity=False),
    importance=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    n_access=st.integers(min_value=0, max_value=100_000),
    elapsed_seconds=st.floats(min_value=0.0, max_value=3.156e9, allow_nan=False, allow_infinity=False),
)
@h_settings(max_examples=500, deadline=1000)
def test_retention_always_in_unit_interval(
    s_init: float, importance: float, n_access: int, elapsed_seconds: float
) -> None:
    """Retention must always be in [0.0, 1.0] for any numeric inputs."""
    from datetime import datetime, timedelta, timezone

    from src.memory.decay import EbbinghausMemoryManager
    from src.models import MemoryNode

    manager = EbbinghausMemoryManager(s_init=s_init)
    now = datetime.now(timezone.utc)
    last_retrieved = now - timedelta(seconds=elapsed_seconds)
    node = MemoryNode(
        memory_id=str(uuid.uuid4()),
        created_at=last_retrieved,
        last_retrieved_at=last_retrieved,
        agent_role="tester",
        summary="property test",
        importance_score=importance,
        retrieval_count=n_access,
    )
    retention = manager.calculate_retention(node)
    assert not math.isnan(retention), "retention must not be NaN"
    assert 0.0 <= retention <= 1.0, f"retention {retention} out of [0, 1] range"


# ── Embedding properties ──────────────────────────────────────────────────────


@given(
    texts=st.lists(
        st.text(min_size=1, max_size=200, alphabet=st.characters(blacklist_categories=("Cs",))),
        min_size=1,
        max_size=20,
    )
)
@h_settings(max_examples=100, deadline=2000)
def test_deterministic_embeddings_unit_norm(texts: list[str]) -> None:
    """Deterministic embeddings must be unit vectors (‖v‖ ≈ 1.0) for any input."""
    from src.memory.embedding import _deterministic_embed

    for text in texts:
        vec = _deterministic_embed(text, dim=64)
        assert len(vec) == 64
        norm = math.sqrt(sum(v * v for v in vec))
        assert abs(norm - 1.0) < 1e-6, f"norm={norm} for {text!r:.40}"


# ── Security properties ───────────────────────────────────────────────────────


@given(
    segments=st.lists(
        st.text(
            min_size=1,
            max_size=20,
            alphabet=st.characters(
                whitelist_categories=("Ll", "Lu", "Nd"),
                whitelist_characters="_-.",
            ),
        ),
        min_size=1,
        max_size=8,
    )
)
@h_settings(max_examples=200, deadline=1000)
def test_contain_path_result_always_inside_root(segments: list[str]) -> None:
    """contain_path, when it succeeds, must return an absolute path inside the workspace root."""
    from src.security import PathSecurityError, contain_path

    with tempfile.TemporaryDirectory() as root:
        candidate = os.path.join(root, *segments)
        try:
            result = contain_path(candidate, root=root)
            assert os.path.isabs(result)
            real_root = os.path.realpath(root)
            assert os.path.commonpath([real_root, result]) == real_root, (
                f"contain_path returned {result!r} escaping {real_root!r}"
            )
        except PathSecurityError:
            pass  # rejection is always acceptable


# ── Compensation-contract properties ────────────────────────────────────────


@given(
    max_balance=st.integers(min_value=1, max_value=5),
    max_amount=st.integers(min_value=1, max_value=5),
)
@h_settings(max_examples=50, deadline=2000)
def test_bounded_contract_verifier_matches_executable_add_subtract_model(max_balance: int, max_amount: int) -> None:
    from dataclasses import replace

    from src.contracts import (
        ArithmeticOperator,
        Assignment,
        BinaryExpression,
        BoolConstant,
        BoundedContractVerifier,
        Comparison,
        ComparisonOperator,
        CompensationContract,
        ProofStatus,
        Transition,
        ValueType,
        VariableSpec,
        input_value,
        original,
        state,
    )

    contract = CompensationContract(
        name="property-add-subtract",
        state_variables=(VariableSpec("balance", ValueType.INTEGER, minimum=0, maximum=max_balance),),
        input_variables=(VariableSpec("amount", ValueType.INTEGER, minimum=0, maximum=max_amount),),
        precondition=Comparison(ComparisonOperator.LE, input_value("amount"), state("balance")),
        forward=Transition(
            (
                Assignment(
                    "balance",
                    BinaryExpression(ArithmeticOperator.SUBTRACT, state("balance"), input_value("amount")),
                ),
            )
        ),
        postcondition=BoolConstant(True),
        compensation=Transition(
            (
                Assignment(
                    "balance",
                    BinaryExpression(ArithmeticOperator.ADD, state("balance"), input_value("amount")),
                ),
            )
        ),
        restoration=Comparison(ComparisonOperator.EQ, state("balance"), original("balance")),
        implementation_identity="hypothesis.add-subtract:v1",
    )
    broken = replace(contract, compensation=contract.forward)

    assert BoundedContractVerifier().verify(contract).status is ProofStatus.PROVED
    assert BoundedContractVerifier().verify(broken).status is ProofStatus.REJECTED
