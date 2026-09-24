"""
SagaMind Z3 Formal Logic Verifier
=================================

Neuro-symbolic safety gate. Each proposed action carries an SMT-LIB2 invariant; the
verifier proves that the action's concrete arguments cannot violate it before execution.

Method (the academically correct refutation procedure)
------------------------------------------------------
1. Declare an SMT constant for every action argument, typed from its Python value.
2. Constrain each constant to its concrete value.
3. Parse the caller-supplied invariant (arbitrary SMT-LIB2) against those declarations.
4. Assert the **negation** of the invariant and ``check-sat``:
   * ``sat``   → a model violates the invariant → reject with the counter-example,
   * ``unsat`` → the invariant is entailed by the arguments → accept,
   * ``unknown``/timeout → **fail closed** (reject).

When the ``z3-solver`` package is absent the verifier degrades to a conservative semantic
guard that enforces only canonical workspace path containment.  The structured
``verify_detailed`` API rejects non-empty SMT policies when no solver is available.
"""

from __future__ import annotations

import enum
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any

from src.config import settings
from src.security import PathSecurityError, contain_path

logger = logging.getLogger("SagaMind.Verifier.Z3")


class VerificationStatus(str, enum.Enum):
    """Machine-readable outcome for policy verification."""

    SAFE = "safe"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"


@dataclass(frozen=True)
class PolicyVerificationResult:
    """Structured policy result for counterexample-guided agent repair."""

    allowed: bool
    status: VerificationStatus
    explanation: str
    violated_property: str | None = None
    counterexample: dict[str, Any] | None = None
    repair_constraints: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result


class Z3Verifier:
    """Formal logic prover using the Z3 SMT solver Python bindings."""

    def __init__(self) -> None:
        self.z3_active = False
        try:
            import z3  # noqa: F401

            self.z3_active = True
            logger.info("Z3 Solver Python bindings successfully loaded.")
        except ImportError:
            logger.warning("z3-solver package not installed. Running semantic validation fallback.")

    def verify(self, action_args: dict[str, Any], invariants_string: str) -> tuple[bool, str]:
        """Prove that *action_args* satisfy *invariants_string*.

        Returns ``(is_safe, explanation)``.
        """
        if not self.z3_active:
            return self._fallback_verify(action_args)
        return self._z3_verify(action_args, invariants_string)

    def verify_detailed(self, action_args: dict[str, Any], invariants_string: str) -> PolicyVerificationResult:
        """Verify a policy with explicit fail-closed statuses and repair data.

        Unlike the compatibility ``verify`` method, this API never silently skips
        unsupported values and never treats an unavailable solver as a proof.
        """

        unsupported = sorted(key for key, value in action_args.items() if not self._supported_scalar(value))
        if unsupported:
            return PolicyVerificationResult(
                allowed=False,
                status=VerificationStatus.UNSUPPORTED,
                explanation=f"Unsupported action argument types for: {', '.join(unsupported)}; rejected.",
            )
        if not invariants_string or not invariants_string.strip():
            ok, explanation = self._fallback_verify(action_args)
            return PolicyVerificationResult(
                allowed=ok,
                status=VerificationStatus.SAFE if ok else VerificationStatus.REJECTED,
                explanation=explanation,
            )
        if not self.z3_active:
            return PolicyVerificationResult(
                allowed=False,
                status=VerificationStatus.UNKNOWN,
                explanation="SMT solver is unavailable; non-empty policy rejected without a proof.",
                violated_property=invariants_string,
            )
        return self._z3_verify_detailed(action_args, invariants_string)

    # ── Z3 path ─────────────────────────────────────────────────────────
    def _z3_verify(self, action_args: dict[str, Any], invariants_string: str) -> tuple[bool, str]:
        import z3

        solver = z3.Solver()
        solver.set("timeout", settings.z3_timeout_ms)

        decls: dict[str, Any] = {}
        for key, val in action_args.items():
            var = self._declare(z3, key, val)
            if var is None:
                continue
            decls[key] = var
            solver.add(var == self._literal(z3, val))

        invariant = self._parse_invariant(z3, invariants_string, decls)
        if invariant is None:
            # No (parseable) invariant supplied: nothing to refute → accept.
            return True, "No invariant supplied; action admitted."

        # Refutation: look for an assignment that satisfies the args but breaks the invariant.
        solver.add(z3.Not(invariant))
        result = solver.check()

        if result == z3.sat:
            model = solver.model()
            counter_example = {str(d): str(model[d]) for d in model.decls()}
            logger.error("Safety verification failed. Counter-example: %s", counter_example)
            return False, f"Safety constraint violation. Counter-example state: {counter_example}"
        if result == z3.unsat:
            logger.info("Invariants proved. Action is formally safe.")
            return True, "Verification successful."
        # unknown / timeout → fail closed.
        logger.warning("Z3 returned 'unknown' (timeout=%dms). Failing closed.", settings.z3_timeout_ms)
        return False, "SMT solver could not resolve the invariant within the timeout; rejected."

    def _z3_verify_detailed(self, action_args: dict[str, Any], invariants_string: str) -> PolicyVerificationResult:
        import z3

        solver = z3.Solver()
        solver.set("timeout", settings.z3_timeout_ms)
        decls = {key: self._declare(z3, key, value) for key, value in action_args.items()}
        for key, variable in decls.items():
            solver.add(variable == self._literal(z3, action_args[key]))
        try:
            assertions = z3.parse_smt2_string(invariants_string, decls=decls)
        except z3.Z3Exception as exc:
            return PolicyVerificationResult(
                allowed=False,
                status=VerificationStatus.INVALID,
                explanation=f"Policy is invalid SMT-LIB2 and was rejected: {exc}",
                violated_property=invariants_string,
            )
        if len(assertions) == 0:
            return PolicyVerificationResult(
                allowed=False,
                status=VerificationStatus.INVALID,
                explanation="Policy contains no assertions; rejected.",
                violated_property=invariants_string,
            )
        invariant = z3.And(*assertions) if len(assertions) > 1 else assertions[0]
        solver.add(z3.Not(invariant))
        outcome = solver.check()
        if outcome == z3.unsat:
            return PolicyVerificationResult(
                allowed=True,
                status=VerificationStatus.SAFE,
                explanation="Verification successful.",
            )
        if outcome == z3.sat:
            counterexample = dict(action_args)
            return PolicyVerificationResult(
                allowed=False,
                status=VerificationStatus.REJECTED,
                explanation="Safety constraint violation.",
                violated_property=invariants_string,
                counterexample=counterexample,
                repair_constraints=tuple(_derive_repair_constraints(invariants_string, action_args)),
            )
        return PolicyVerificationResult(
            allowed=False,
            status=VerificationStatus.UNKNOWN,
            explanation="SMT solver returned unknown or timed out; rejected.",
            violated_property=invariants_string,
        )

    @staticmethod
    def _declare(z3: Any, key: str, val: Any) -> Any:
        if isinstance(val, bool):
            return z3.Bool(key)
        if isinstance(val, str):
            return z3.String(key)
        if isinstance(val, (int, float)):
            return z3.Real(key)
        return None

    @staticmethod
    def _supported_scalar(value: Any) -> bool:
        return isinstance(value, (bool, int, float, str))

    @staticmethod
    def _literal(z3: Any, val: Any) -> Any:
        if isinstance(val, bool):
            return z3.BoolVal(val)
        if isinstance(val, str):
            return z3.StringVal(val)
        return z3.RealVal(val)

    @staticmethod
    def _parse_invariant(z3: Any, invariants_string: str, decls: dict[str, Any]) -> Any:
        """Parse arbitrary SMT-LIB2 assertions, binding declared argument constants."""
        if not invariants_string or not invariants_string.strip():
            return None
        try:
            assertions = z3.parse_smt2_string(invariants_string, decls=decls)
            if len(assertions) == 0:
                return None
            return z3.And(*assertions) if len(assertions) > 1 else assertions[0]
        except z3.Z3Exception as exc:
            logger.error("Failed to parse SMT-LIB2 invariant: %s", exc)
            # Unparseable invariant must not silently pass → treat as unsatisfiable guard.
            return z3.BoolVal(False)

    # ── Fallback path (z3 unavailable) ──────────────────────────────────
    def _fallback_verify(self, action_args: dict[str, Any]) -> tuple[bool, str]:
        """Conservative semantic guard using canonical filesystem containment."""
        if "path" in action_args:
            path = action_args["path"]
            try:
                contain_path(path, root=settings.allowed_workspace_root)
            except (PathSecurityError, TypeError) as exc:
                return False, f"Semantic Guard Fail: {exc}"
        return True, "Semantic validation succeeded (SMT solver unavailable)."


_SIMPLE_COMPARISON = re.compile(
    r"\((<=|<|>=|>|=)\s+([A-Za-z_][A-Za-z0-9_]*)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*|-?\d+(?:\.\d+)?)\)"
)


def _derive_repair_constraints(invariants_string: str, action_args: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract useful retry bounds from simple violated SMT comparisons.

    Arbitrary SMT remains supported by the solver.  For predicates outside this
    deliberately conservative subset the result still includes a counterexample,
    but does not invent a potentially wrong repair.
    """

    constraints: list[dict[str, Any]] = []
    operator_names = {
        "<=": "maximum",
        "<": "exclusiveMaximum",
        ">=": "minimum",
        ">": "exclusiveMinimum",
        "=": "const",
    }
    for operator, field, raw_limit in _SIMPLE_COMPARISON.findall(invariants_string):
        if field not in action_args:
            continue
        if raw_limit in action_args:
            limit: Any = {"field": raw_limit}
            concrete_limit = action_args[raw_limit]
        else:
            limit = float(raw_limit) if "." in raw_limit else int(raw_limit)
            concrete_limit = limit
        try:
            violated = {
                "<=": action_args[field] > concrete_limit,
                "<": action_args[field] >= concrete_limit,
                ">=": action_args[field] < concrete_limit,
                ">": action_args[field] <= concrete_limit,
                "=": action_args[field] != concrete_limit,
            }[operator]
        except TypeError:
            continue
        if violated:
            constraints.append(
                {
                    "field": field,
                    "constraint": operator_names[operator],
                    "value": limit,
                    "predicate": f"{field} {operator} {raw_limit}",
                }
            )
    return constraints
