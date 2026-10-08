"""Built-in constants and functions, and compile-time evaluation of expressions."""

from __future__ import annotations

import cmath
import math
import operator
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .. import ast

__all__ = [
    "BUILTIN_CONSTANTS",
    "BUILTIN_FUNCTIONS",
    "OPENQASM2_FUNCTIONS",
    "Bits",
    "NotConstant",
    "CannotEvaluate",
    "Evaluator",
]

#: Constants defined in every program, with their values.
BUILTIN_CONSTANTS = {
    "pi": math.pi,
    "π": math.pi,
    "tau": math.tau,
    "τ": math.tau,
    "euler": math.e,
    "ℇ": math.e,
}


@dataclass(frozen=True)
class Bits:
    """The value of a ``bit[width]``; bit ``i`` of the register is bit ``i`` of ``value``."""

    value: int
    width: int

    def __int__(self) -> int:
        return self.value


Value = int | float | complex | bool | Bits


class NotConstant(Exception):
    """The expression depends on a value only known at run time."""

    def __init__(self, node: ast.Node):
        super().__init__(node)
        self.node = node


class CannotEvaluate(Exception):
    """The expression is constant but this evaluator does not compute it."""


def _to_int(value: Value) -> int:
    """Convert like a C cast: bits are read as unsigned, floats are truncated."""
    if isinstance(value, Bits):
        return value.value
    if isinstance(value, complex):
        raise CannotEvaluate()
    return int(value)


def _to_float(value: Value) -> float:
    if isinstance(value, Bits):
        return float(value.value)
    if isinstance(value, complex):
        raise CannotEvaluate()
    return float(value)


def _to_complex(value: Value) -> complex:
    return complex(value.value) if isinstance(value, Bits) else complex(value)


def _rotate(bits: Value, distance: Value, left: bool) -> Value:
    value = _to_int(bits)
    width = bits.width if isinstance(bits, Bits) else max(value.bit_length(), 1)
    shift = _to_int(distance) % width if width else 0
    if not left:
        shift = (width - shift) % width if width else 0
    mask = (1 << width) - 1
    rotated = ((value << shift) | (value >> (width - shift))) & mask
    return Bits(rotated, width) if isinstance(bits, Bits) else rotated


def _popcount(bits: Value) -> int:
    value = _to_int(bits)
    if isinstance(bits, Bits):
        value &= (1 << bits.width) - 1
    return bin(value).count("1")


def _mod(a: Value, b: Value) -> Value:
    if isinstance(a, int) and isinstance(b, int):
        return a - b * int(a / b)  # C-like remainder, sign of the dividend
    return math.fmod(_to_float(a), _to_float(b))


def _pow(a: Value, b: Value) -> Value:
    if isinstance(a, complex) or isinstance(b, complex):
        return _to_complex(a) ** _to_complex(b)
    if isinstance(a, int) and isinstance(b, int) and b >= 0:
        return a**b
    return _to_float(a) ** _to_float(b)


def _real_math(real: Callable, complex_: Callable) -> Callable:
    def apply(x: Value) -> Value:
        if isinstance(x, complex):
            return complex_(x)
        return real(_to_float(x))

    return apply


#: ``name: (minimum arguments, maximum arguments, implementation)``. A ``None``
#: implementation is not evaluated at compile time.
BUILTIN_FUNCTIONS: dict[str, tuple[int, int, Callable[..., Value] | None]] = {
    "arccos": (1, 1, _real_math(math.acos, cmath.acos)),
    "arcsin": (1, 1, _real_math(math.asin, cmath.asin)),
    "arctan": (1, 1, _real_math(math.atan, cmath.atan)),
    "ceiling": (1, 1, lambda x: math.ceil(_to_float(x))),
    "cos": (1, 1, _real_math(math.cos, cmath.cos)),
    "exp": (1, 1, _real_math(math.exp, cmath.exp)),
    "floor": (1, 1, lambda x: math.floor(_to_float(x))),
    "log": (1, 1, _real_math(math.log, cmath.log)),
    "mod": (2, 2, _mod),
    "popcount": (1, 1, _popcount),
    "pow": (2, 2, _pow),
    "rotl": (2, 2, lambda a, n: _rotate(a, n, left=True)),
    "rotr": (2, 2, lambda a, n: _rotate(a, n, left=False)),
    "sin": (1, 1, _real_math(math.sin, cmath.sin)),
    "sqrt": (1, 1, _real_math(math.sqrt, cmath.sqrt)),
    "tan": (1, 1, _real_math(math.tan, cmath.tan)),
    "real": (1, 1, lambda z: _to_complex(z).real),
    "imag": (1, 1, lambda z: _to_complex(z).imag),
    "sizeof": (1, 2, None),
}

#: The functions of OpenQASM 2 (``ln`` is the natural logarithm).
OPENQASM2_FUNCTIONS: dict[str, tuple[int, int, Callable[..., Value] | None]] = {
    "sin": BUILTIN_FUNCTIONS["sin"],
    "cos": BUILTIN_FUNCTIONS["cos"],
    "tan": BUILTIN_FUNCTIONS["tan"],
    "exp": BUILTIN_FUNCTIONS["exp"],
    "ln": BUILTIN_FUNCTIONS["log"],
    "sqrt": BUILTIN_FUNCTIONS["sqrt"],
    # Accepted by most OpenQASM 2 implementations although not in the paper.
    "asin": BUILTIN_FUNCTIONS["arcsin"],
    "acos": BUILTIN_FUNCTIONS["arccos"],
    "atan": BUILTIN_FUNCTIONS["arctan"],
}


_BINARY: dict[ast.BinaryOperator, Callable[[Any, Any], Any]] = {
    ast.BinaryOperator.ADD: operator.add,
    ast.BinaryOperator.SUBTRACT: operator.sub,
    ast.BinaryOperator.MULTIPLY: operator.mul,
    ast.BinaryOperator.POWER: _pow,
    ast.BinaryOperator.MODULO: _mod,
    ast.BinaryOperator.LESS: operator.lt,
    ast.BinaryOperator.LESS_EQUAL: operator.le,
    ast.BinaryOperator.GREATER: operator.gt,
    ast.BinaryOperator.GREATER_EQUAL: operator.ge,
    ast.BinaryOperator.EQUAL: operator.eq,
    ast.BinaryOperator.NOT_EQUAL: operator.ne,
    ast.BinaryOperator.SHIFT_LEFT: operator.lshift,
    ast.BinaryOperator.SHIFT_RIGHT: operator.rshift,
    ast.BinaryOperator.BIT_AND: operator.and_,
    ast.BinaryOperator.BIT_OR: operator.or_,
    ast.BinaryOperator.BIT_XOR: operator.xor,
}


class Evaluator:
    """Evaluate constant expressions.

    ``lookup(name)`` returns the value of a constant, raises
    :class:`NotConstant` for run-time values, or :class:`CannotEvaluate` when
    the value is unknown. ``functions`` maps built-in function names to their
    arity and implementation.
    """

    def __init__(
        self,
        lookup: Callable[[ast.Identifier], Value],
        functions: dict[str, tuple[int, int, Callable[..., Value] | None]] = BUILTIN_FUNCTIONS,
        sizeof: Callable[[ast.Expression, int | None], int] | None = None,
    ):
        self.lookup = lookup
        self.functions = functions
        self.sizeof = sizeof

    def __call__(self, node: ast.Node) -> Value:
        return self.evaluate(node)

    def evaluate(self, node: ast.Node) -> Value:
        if isinstance(node, (ast.IntegerLiteral, ast.FloatLiteral, ast.BooleanLiteral)):
            return node.value
        if isinstance(node, ast.ImaginaryLiteral):
            return complex(0, node.value)
        if isinstance(node, ast.BitstringLiteral):
            return Bits(node.value, node.width)
        if isinstance(node, ast.Identifier):
            return self.lookup(node)
        if isinstance(node, ast.UnaryExpression):
            return self.unary(node)
        if isinstance(node, ast.BinaryExpression):
            return self.binary(node)
        if isinstance(node, ast.FunctionCall):
            return self.call(node)
        if isinstance(node, ast.Cast):
            return self.cast(node)
        if isinstance(node, ast.IndexExpression):
            return self.index(node)
        if isinstance(node, (ast.MeasureExpression, ast.QuantumCall, ast.HardwareQubit)):
            raise NotConstant(node)
        raise CannotEvaluate()

    def integer(self, node: ast.Node) -> int:
        """Evaluate an expression that must produce an integer."""
        value = self.evaluate(node)
        if isinstance(value, Bits):
            return value.value
        if isinstance(value, bool) or not isinstance(value, int):
            raise CannotEvaluate()
        return value

    def unary(self, node: ast.UnaryExpression) -> Value:
        value = self.evaluate(node.operand)
        if node.op is ast.UnaryOperator.NEGATE:
            if isinstance(value, Bits):
                raise CannotEvaluate()
            return -value
        if node.op is ast.UnaryOperator.LOGICAL_NOT:
            return not _truthy(value)
        if isinstance(value, Bits):
            return Bits(~value.value & ((1 << value.width) - 1), value.width)
        if isinstance(value, int) and not isinstance(value, bool):
            return ~value
        raise CannotEvaluate()

    def binary(self, node: ast.BinaryExpression) -> Value:
        op = node.op
        if op is ast.BinaryOperator.LOGICAL_AND:
            return _truthy(self.evaluate(node.lhs)) and _truthy(self.evaluate(node.rhs))
        if op is ast.BinaryOperator.LOGICAL_OR:
            return _truthy(self.evaluate(node.lhs)) or _truthy(self.evaluate(node.rhs))
        lhs, rhs = self.evaluate(node.lhs), self.evaluate(node.rhs)
        if isinstance(lhs, Bits) or isinstance(rhs, Bits):
            return self.bits_binary(op, lhs, rhs)
        try:
            if op is ast.BinaryOperator.DIVIDE:
                if isinstance(lhs, int) and isinstance(rhs, int):
                    if rhs == 0:
                        raise CannotEvaluate()
                    quotient = abs(lhs) // abs(rhs)
                    return quotient if (lhs >= 0) == (rhs >= 0) else -quotient
                return lhs / rhs
            if op in (ast.BinaryOperator.MODULO,) and rhs == 0:
                raise CannotEvaluate()
            return _BINARY[op](lhs, rhs)
        except (TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
            raise CannotEvaluate() from exc

    def bits_binary(self, op: ast.BinaryOperator, lhs: Value, rhs: Value) -> Value:
        if isinstance(lhs, Bits) and op in (
            ast.BinaryOperator.SHIFT_LEFT,
            ast.BinaryOperator.SHIFT_RIGHT,
        ):
            mask = (1 << lhs.width) - 1
            shifted = (
                (lhs.value << _to_int(rhs))
                if op is ast.BinaryOperator.SHIFT_LEFT
                else (lhs.value >> _to_int(rhs))
            )
            return Bits(shifted & mask, lhs.width)
        if (
            isinstance(lhs, Bits)
            and isinstance(rhs, Bits)
            and op
            in (
                ast.BinaryOperator.BIT_AND,
                ast.BinaryOperator.BIT_OR,
                ast.BinaryOperator.BIT_XOR,
            )
        ):
            return Bits(_BINARY[op](lhs.value, rhs.value), max(lhs.width, rhs.width))
        if op in (ast.BinaryOperator.EQUAL, ast.BinaryOperator.NOT_EQUAL):
            return _BINARY[op](_to_int(lhs), _to_int(rhs))
        raise CannotEvaluate()

    def call(self, node: ast.FunctionCall) -> Value:
        name = node.name.name
        if name == "sizeof" and self.sizeof is not None:
            dimension = self.integer(node.arguments[1]) if len(node.arguments) > 1 else None
            return self.sizeof(node.arguments[0], dimension)
        entry = self.functions.get(name)
        if entry is None or entry[2] is None:
            raise CannotEvaluate()
        arguments = [self.evaluate(argument) for argument in node.arguments]
        try:
            return entry[2](*arguments)
        except (TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
            raise CannotEvaluate() from exc

    def cast(self, node: ast.Cast) -> Value:
        value = self.evaluate(node.operand)
        target = node.type
        if isinstance(target, ast.BoolType):
            return _truthy(value)
        if isinstance(target, (ast.IntType, ast.UintType)):
            return _to_int(value)
        if isinstance(target, ast.FloatType):
            return _to_float(value)
        if isinstance(target, ast.ComplexType):
            return _to_complex(value)
        if isinstance(target, ast.BitType) and target.size is not None:
            width = self.integer(target.size)
            if isinstance(value, (int, Bits)):
                return Bits(_to_int(value) & ((1 << width) - 1), width)
        raise CannotEvaluate()

    def index(self, node: ast.IndexExpression) -> Value:
        value = self.evaluate(node.collection)
        if not isinstance(value, Bits) or not isinstance(node.index, list) or len(node.index) != 1:
            raise CannotEvaluate()
        element = node.index[0]
        if isinstance(element, ast.Range):
            raise CannotEvaluate()
        position = self.integer(element)
        if position < 0:
            position += value.width
        if not 0 <= position < value.width:
            raise CannotEvaluate()
        return (value.value >> position) & 1 == 1


def _truthy(value: Value) -> bool:
    if isinstance(value, Bits):
        return value.value != 0
    return bool(value)
