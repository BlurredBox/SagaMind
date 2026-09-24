"""Typed DSL for reversible tool contracts.

The DSL is intentionally small.  A small, total language is easier to audit than
executing Python callbacks and permits both exhaustive bounded verification and a
future SMT backend to share exactly the same semantics.
"""

from __future__ import annotations

import enum
import re
from dataclasses import asdict, dataclass, field
from typing import Any, TypeAlias, cast

Scalar: TypeAlias = bool | int | float | str

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ValueType(str, enum.Enum):
    INTEGER = "integer"
    REAL = "real"
    BOOLEAN = "boolean"
    STRING = "string"


class RefSource(str, enum.Enum):
    """State visible at the current phase, original state, or action input."""

    STATE = "state"
    ORIGINAL = "original"
    INPUT = "input"


class ArithmeticOperator(str, enum.Enum):
    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"


class ComparisonOperator(str, enum.Enum):
    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"


@dataclass(frozen=True)
class VariableSpec:
    """A typed variable with a finite verification domain.

    Integer domains may be expressed with inclusive bounds.  Real and string
    variables require explicit ``allowed_values`` because their natural domains
    are not finite.  Boolean variables need no domain declaration.
    """

    name: str
    value_type: ValueType
    minimum: int | None = None
    maximum: int | None = None
    allowed_values: tuple[Scalar, ...] = ()

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.name):
            raise ValueError(f"Invalid variable name: {self.name!r}")
        if (self.minimum is None) != (self.maximum is None):
            raise ValueError("minimum and maximum must be supplied together")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum cannot exceed maximum")


@dataclass(frozen=True)
class Ref:
    name: str
    source: RefSource = RefSource.STATE


@dataclass(frozen=True)
class Literal:
    value: Scalar


@dataclass(frozen=True)
class BinaryExpression:
    operator: ArithmeticOperator
    left: Expression
    right: Expression


Expression: TypeAlias = Ref | Literal | BinaryExpression


@dataclass(frozen=True)
class BoolConstant:
    value: bool


@dataclass(frozen=True)
class Comparison:
    operator: ComparisonOperator
    left: Expression
    right: Expression


@dataclass(frozen=True)
class AllOf:
    conditions: tuple[Condition, ...]


@dataclass(frozen=True)
class AnyOf:
    conditions: tuple[Condition, ...]


@dataclass(frozen=True)
class Not:
    condition: Condition


Condition: TypeAlias = BoolConstant | Comparison | AllOf | AnyOf | Not


@dataclass(frozen=True)
class Assignment:
    target: str
    value: Expression


@dataclass(frozen=True)
class Transition:
    """Simultaneous state assignments; omitted variables retain their value."""

    assignments: tuple[Assignment, ...] = ()


@dataclass(frozen=True)
class CompensationContract:
    """Complete, typed specification of a reversible state transition."""

    name: str
    state_variables: tuple[VariableSpec, ...]
    input_variables: tuple[VariableSpec, ...]
    precondition: Condition
    forward: Transition
    postcondition: Condition
    compensation: Transition
    restoration: Condition
    implementation_identity: str
    version: str = "1"
    irreversible_effects: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Contract name cannot be empty")

    def to_dict(self) -> dict[str, Any]:
        """Return a stable, JSON-compatible representation."""

        return cast(dict[str, Any], _enum_values(asdict(self)))


def _enum_values(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _enum_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_enum_values(item) for item in value]
    return value


def state(name: str) -> Ref:
    return Ref(name, RefSource.STATE)


def original(name: str) -> Ref:
    return Ref(name, RefSource.ORIGINAL)


def input_value(name: str) -> Ref:
    return Ref(name, RefSource.INPUT)


def literal(value: Scalar) -> Literal:
    return Literal(value)
