"""Types used by the semantic analyser.

These describe *resolved* types: sizes are integers once the designator has
been evaluated. A size is ``None`` for scalars (``bit``, a single ``qubit``)
and :data:`UNKNOWN_SIZE` when a register's size could not be evaluated.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

__all__ = [
    "UNKNOWN_SIZE",
    "Type",
    "Unknown",
    "Void",
    "Bool",
    "Bit",
    "Int",
    "Float",
    "Angle",
    "Complex",
    "Duration",
    "Stretch",
    "Qubit",
    "Array",
    "is_classical_scalar",
    "is_numeric",
    "is_integer",
]

UNKNOWN_SIZE = -1


@dataclass(frozen=True)
class Type:
    const: bool = field(default=False, kw_only=True)

    def as_const(self, const: bool = True) -> Type:
        return replace(self, const=const)

    def __str__(self) -> str:
        return ("const " if self.const else "") + self.describe()

    def describe(self) -> str:
        return type(self).__name__.lower()


@dataclass(frozen=True)
class Unknown(Type):
    """A type that could not be determined; never causes further errors."""

    def describe(self) -> str:
        return "<unknown>"


@dataclass(frozen=True)
class Void(Type):
    pass


@dataclass(frozen=True)
class Bool(Type):
    pass


def _sized(name: str, size: int | None) -> str:
    if size is None:
        return name
    return f"{name}[{'?' if size == UNKNOWN_SIZE else size}]"


@dataclass(frozen=True)
class Bit(Type):
    """``bit`` (``size=None``) or the register ``bit[size]``."""

    size: int | None = None

    def describe(self) -> str:
        return _sized("bit", self.size)


@dataclass(frozen=True)
class Int(Type):
    width: int | None = None
    signed: bool = True

    def describe(self) -> str:
        return _sized("int" if self.signed else "uint", self.width)


@dataclass(frozen=True)
class Float(Type):
    width: int | None = None

    def describe(self) -> str:
        return _sized("float", self.width)


@dataclass(frozen=True)
class Angle(Type):
    width: int | None = None

    def describe(self) -> str:
        return _sized("angle", self.width)


@dataclass(frozen=True)
class Complex(Type):
    width: int | None = None

    def describe(self) -> str:
        return "complex" if self.width is None else f"complex[{_sized('float', self.width)}]"


@dataclass(frozen=True)
class Duration(Type):
    pass


@dataclass(frozen=True)
class Stretch(Type):
    pass


@dataclass(frozen=True)
class Qubit(Type):
    """A single qubit (``size=None``) or a qubit register."""

    size: int | None = None

    def describe(self) -> str:
        return _sized("qubit", self.size)


@dataclass(frozen=True)
class Array(Type):
    """``array[base, d0, d1, ...]``; unknown dimensions are :data:`UNKNOWN_SIZE`."""

    base: Type = Unknown()
    dimensions: tuple[int, ...] = ()

    def describe(self) -> str:
        dims = ", ".join("?" if d == UNKNOWN_SIZE else str(d) for d in self.dimensions)
        return f"array[{self.base.describe()}, {dims}]"


def is_classical_scalar(type_: Type) -> bool:
    return isinstance(type_, (Bool, Bit, Int, Float, Angle, Complex, Duration, Stretch))


def is_numeric(type_: Type) -> bool:
    """Types usable in arithmetic: integers, floats, angles, complex, bool and bit."""
    return isinstance(type_, (Bool, Bit, Int, Float, Angle, Complex))


def is_integer(type_: Type) -> bool:
    return isinstance(type_, (Int, Bool)) or (isinstance(type_, Bit) and type_.size is None)
