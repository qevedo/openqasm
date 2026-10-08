"""Semantic analysis: names, scopes, signatures and types.

The analyser walks a parsed program, resolves every identifier according to
the scoping rules of the specification, follows ``include`` statements and
checks the rules that the grammar cannot express. It reports clear
violations only; constructs whose validity depends on run-time values or on
implementation-defined behaviour are accepted.
"""

from __future__ import annotations

import enum
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib import resources
from typing import TypeGuard

from .. import ast
from ..errors import QasmError, QasmIncludeError, QasmSemanticError
from ..parser import parse
from . import types as t
from .constants import (
    BUILTIN_CONSTANTS,
    BUILTIN_FUNCTIONS,
    OPENQASM2_FUNCTIONS,
    Bits,
    CannotEvaluate,
    Evaluator,
    NotConstant,
)

__all__ = [
    "analyze",
    "Analysis",
    "Symbol",
    "SymbolKind",
    "GateSignature",
    "SubroutineSignature",
    "Scope",
]

# Gates of the qelib1.inc published with the OpenQASM 2.0 specification. The
# bundled qelib1.inc has more (sx, rxx, cu, ...); programs may redefine those.
_SPEC_QELIB1_GATES = frozenset(
    [
        "u3",
        "u2",
        "u1",
        "cx",
        "id",
        "x",
        "y",
        "z",
        "h",
        "s",
        "sdg",
        "t",
        "tdg",
        "rx",
        "ry",
        "rz",
        "cz",
        "cy",
        "ch",
        "ccx",
        "crz",
        "cu1",
        "cu3",
    ]
)
_LIBRARIES = ("stdgates.inc", "qelib1.inc")


class SymbolKind(enum.Enum):
    VARIABLE = "variable"
    CONSTANT = "constant"
    INPUT = "input"
    OUTPUT = "output"
    QUBIT = "qubit"
    ALIAS = "alias"
    LOOP_VARIABLE = "loop variable"
    ARGUMENT = "argument"
    GATE_PARAMETER = "gate parameter"
    GATE_QUBIT = "gate qubit"
    GATE = "gate"
    SUBROUTINE = "subroutine"
    EXTERN = "extern"
    BUILTIN_CONSTANT = "built-in constant"
    BUILTIN_FUNCTION = "built-in function"
    BUILTIN_GATE = "built-in gate"


_CALLABLE_KINDS = frozenset({SymbolKind.SUBROUTINE, SymbolKind.EXTERN, SymbolKind.BUILTIN_FUNCTION})
_GATE_KINDS = frozenset({SymbolKind.GATE, SymbolKind.BUILTIN_GATE})
# Symbols visible inside gate and subroutine bodies from the enclosing scope.
_VISIBLE_EVERYWHERE = frozenset(
    {
        SymbolKind.CONSTANT,
        SymbolKind.GATE,
        SymbolKind.SUBROUTINE,
        SymbolKind.EXTERN,
        SymbolKind.BUILTIN_CONSTANT,
        SymbolKind.BUILTIN_FUNCTION,
        SymbolKind.BUILTIN_GATE,
    }
)


@dataclass(frozen=True)
class GateSignature:
    parameters: int
    qubits: int


@dataclass(frozen=True)
class SubroutineSignature:
    arguments: tuple[t.Type, ...]
    return_type: t.Type


@dataclass
class Symbol:
    """A declared name."""

    name: str
    kind: SymbolKind
    type: t.Type = field(default_factory=t.Unknown)
    span: ast.Span | None = None
    #: The value of constants, when it could be computed.
    value: object = None
    signature: GateSignature | SubroutineSignature | None = None
    #: Library gates that a program may redefine (see the OpenQASM 2 qelib1.inc).
    overridable: bool = False


class Scope:
    """A lexical scope: ``builtin``, ``global``, ``gate``, ``subroutine`` or ``block``."""

    def __init__(self, kind: str, parent: Scope | None = None):
        self.kind = kind
        self.parent = parent
        self.symbols: dict[str, Symbol] = {}

    def __contains__(self, name: str) -> bool:
        return name in self.symbols

    def __iter__(self) -> Iterator[Symbol]:
        return iter(self.symbols.values())


@dataclass
class Analysis:
    """The result of :func:`analyze`."""

    program: ast.Program
    #: Every problem found, in source order. Empty for a valid program.
    errors: list[QasmError]
    #: The global scope after the whole program, including included files.
    globals: Scope
    #: Files read by ``include`` statements (library names for the bundled ones).
    includes: list[str]
    #: Gate names that have a ``defcal``.
    calibrated_gates: set[str]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def gates(self) -> dict[str, GateSignature]:
        """Signatures of the gates defined in the program and its includes."""
        out: dict[str, GateSignature] = {}
        scope: Scope | None = self.globals
        while scope is not None:
            for symbol in scope:
                if symbol.kind in _GATE_KINDS and symbol.name not in out:
                    out[symbol.name] = symbol.signature  # type: ignore[assignment]
            scope = scope.parent
        return out

    @property
    def qubits(self) -> dict[str, int]:
        """Global qubit registers and their sizes (1 for a single qubit, -1 if unknown)."""
        return {
            symbol.name: 1 if symbol.type.size is None else symbol.type.size  # type: ignore[attr-defined]
            for symbol in self.globals
            if symbol.kind is SymbolKind.QUBIT
        }

    def raise_for_errors(self) -> None:
        """Raise the first error, with all of them in its ``errors`` attribute."""
        if self.errors:
            error = self.errors[0]
            error.errors = list(self.errors)  # type: ignore[attr-defined]
            raise error


def _library_source(name: str) -> str:
    return (resources.files("openqasm") / "stdlib" / name).read_text(encoding="utf-8")


class _Analyzer:
    def __init__(
        self,
        program: ast.Program,
        filename: str | None,
        include_paths: Sequence[str | os.PathLike],
    ):
        self.program = program
        self.filename = filename
        self.include_paths = [os.fspath(p) for p in include_paths]
        self.openqasm2 = (program.version or "").startswith("2")
        self.errors: list[QasmError] = []
        self.includes: list[str] = []
        self.calibrated_gates: set[str] = set()
        self._include_stack: list[str] = [os.path.abspath(filename)] if filename else []
        self._file_stack: list[str | None] = [filename]
        self._in_library = False
        self.functions = OPENQASM2_FUNCTIONS if self.openqasm2 else BUILTIN_FUNCTIONS
        self.builtins = self._builtin_scope()
        self.globals = Scope("global", self.builtins)
        self.scope = self.globals
        self.current_gate: str | None = None
        self.return_types: list[t.Type | None] = []
        self.evaluator = Evaluator(self.constant_value, self.functions, self.sizeof)

    def _builtin_scope(self) -> Scope:
        scope = Scope("builtin")
        for name, value in BUILTIN_CONSTANTS.items():
            if self.openqasm2 and name != "pi":
                continue
            scope.symbols[name] = Symbol(
                name, SymbolKind.BUILTIN_CONSTANT, t.Float(const=True), value=value
            )
        for name in self.functions:
            scope.symbols[name] = Symbol(name, SymbolKind.BUILTIN_FUNCTION)
        scope.symbols["U"] = Symbol("U", SymbolKind.BUILTIN_GATE, signature=GateSignature(3, 1))
        if self.openqasm2:
            scope.symbols["CX"] = Symbol(
                "CX", SymbolKind.BUILTIN_GATE, signature=GateSignature(0, 2)
            )
        return scope

    # -- reporting ------------------------------------------------------------

    def error(self, message: str, node: ast.Node | None, kind: type = QasmSemanticError) -> None:
        span = node.span if node is not None else None
        if self._in_library:
            return
        self.errors.append(kind(message, span))

    # -- scopes ---------------------------------------------------------------

    @contextmanager
    def new_scope(self, kind: str) -> Iterator[Scope]:
        scope = Scope(kind, self.scope)
        self.scope = scope
        try:
            yield scope
        finally:
            assert scope.parent is not None
            self.scope = scope.parent

    def lookup(self, name: str) -> tuple[Symbol | None, bool]:
        """Find ``name``; the flag is false if it exists but is not visible here."""
        scope: Scope | None = self.scope
        crossed = False
        while scope is not None:
            symbol = scope.symbols.get(name)
            if symbol is not None:
                return symbol, not crossed or symbol.kind in _VISIBLE_EVERYWHERE
            if scope.kind in ("gate", "subroutine"):
                crossed = True
            scope = scope.parent
        return None, True

    def resolve(self, identifier: ast.Identifier) -> Symbol | None:
        """Look ``identifier`` up, reporting undefined or invisible names."""
        symbol, visible = self.lookup(identifier.name)
        if symbol is None:
            self.error(f"undefined identifier '{identifier.name}'", identifier)
            return None
        if not visible:
            where = "gate" if self.inside("gate") else "subroutine"
            self.error(
                f"'{identifier.name}' is not visible inside a {where}: only constants, gates and"
                " subroutines of the enclosing scope can be used",
                identifier,
            )
            return None
        return symbol

    def inside(self, kind: str) -> bool:
        scope: Scope | None = self.scope
        while scope is not None:
            if scope.kind == kind:
                return True
            scope = scope.parent
        return False

    def declare(self, symbol: Symbol, node: ast.Node) -> None:
        existing = self.scope.symbols.get(symbol.name)
        if existing is not None:
            if existing.overridable and symbol.kind in _GATE_KINDS:
                self.scope.symbols[symbol.name] = symbol
                return
            where = f" (first declared at {existing.span})" if existing.span else ""
            self.error(f"'{symbol.name}' is already declared in this scope{where}", node)
            return
        if self.scope is self.globals and symbol.name in self.builtins:
            self.error(f"cannot redeclare the built-in '{symbol.name}'", node)
            return
        self.scope.symbols[symbol.name] = symbol

    # -- constants ------------------------------------------------------------

    def constant_value(self, identifier: ast.Identifier):
        symbol, visible = self.lookup(identifier.name)
        if symbol is None or not visible:
            raise CannotEvaluate()
        if symbol.kind in (SymbolKind.CONSTANT, SymbolKind.BUILTIN_CONSTANT):
            if symbol.value is None:
                raise CannotEvaluate()
            return symbol.value
        raise NotConstant(identifier)

    def sizeof(self, target: ast.Expression, dimension: int | None) -> int:
        type_ = self.type_of(target, report=False)
        if isinstance(type_, t.Array):
            index = dimension or 0
            if 0 <= index < len(type_.dimensions) and type_.dimensions[index] != t.UNKNOWN_SIZE:
                return type_.dimensions[index]
        elif isinstance(type_, (t.Bit, t.Qubit)) and type_.size not in (None, t.UNKNOWN_SIZE):
            return type_.size  # type: ignore[return-value]
        raise CannotEvaluate()

    def evaluate(self, node: ast.Node):
        """The constant value of ``node``, or ``None`` if it is unknown or not constant."""
        try:
            return self.evaluator(node)
        except (CannotEvaluate, NotConstant):
            return None

    def size(self, node: ast.Expression | None, what: str, allow_zero: bool = False) -> int | None:
        """Evaluate a designator: ``None`` without one, :data:`UNKNOWN_SIZE` if not computable."""
        if node is None:
            return None
        self.type_of(node)
        try:
            value = self.evaluator.integer(node)
        except NotConstant as exc:
            name = getattr(exc.node, "name", None)
            reason = f": '{name}' is not a constant" if name else ""
            self.error(f"{what} must be a constant expression{reason}", node)
            return t.UNKNOWN_SIZE
        except CannotEvaluate:
            return t.UNKNOWN_SIZE
        if value < 0 or (value == 0 and not allow_zero):
            self.error(
                f"{what} must be {'non-negative' if allow_zero else 'positive'}, not {value}", node
            )
            return t.UNKNOWN_SIZE
        return value

    # -- types ----------------------------------------------------------------

    def resolve_type(self, node: ast.Type) -> t.Type:
        if isinstance(node, ast.IntType):
            return t.Int(self.size(node.size, "the width of an int"), signed=True)
        if isinstance(node, ast.UintType):
            return t.Int(self.size(node.size, "the width of a uint"), signed=False)
        if isinstance(node, ast.FloatType):
            return t.Float(self.size(node.size, "the width of a float"))
        if isinstance(node, ast.AngleType):
            return t.Angle(self.size(node.size, "the width of an angle"))
        if isinstance(node, ast.BitType):
            return t.Bit(self.size(node.size, "the size of a bit register"))
        if isinstance(node, ast.BoolType):
            return t.Bool()
        if isinstance(node, ast.ComplexType):
            base = self.resolve_type(node.base) if node.base is not None else None
            return t.Complex(base.width if isinstance(base, t.Float) else None)
        if isinstance(node, ast.DurationType):
            return t.Duration()
        if isinstance(node, ast.StretchType):
            return t.Stretch()
        if isinstance(node, ast.ArrayType):
            base = self.resolve_type(node.base)
            if len(node.dimensions) > 7:
                self.error("arrays can have at most 7 dimensions", node)
            dims = tuple(
                self.size(d, "an array dimension", allow_zero=True) or 0 for d in node.dimensions
            )
            return t.Array(base=base, dimensions=dims)
        if isinstance(node, ast.ArrayReferenceType):
            base = self.resolve_type(node.base)
            if node.rank is not None:
                rank = self.size(node.rank, "the number of array dimensions")
                count = rank if rank not in (None, t.UNKNOWN_SIZE) else 1
                return t.Array(base=base, dimensions=(t.UNKNOWN_SIZE,) * count)
            dims = tuple(
                self.size(d, "an array dimension", allow_zero=True) or 0
                for d in node.dimensions or []
            )
            return t.Array(base=base, dimensions=dims)
        if isinstance(node, ast.QubitType):
            return t.Qubit(self.size(node.size, "the size of a qubit register"))
        return t.Unknown()

    def type_of(self, node: ast.Node, report: bool = True) -> t.Type:
        """Infer the type of an expression, reporting problems if ``report``."""
        if not report:
            errors = len(self.errors)
            try:
                return self._type_of(node)
            finally:
                del self.errors[errors:]
        return self._type_of(node)

    def _type_of(self, node: ast.Node) -> t.Type:
        if isinstance(node, ast.IntegerLiteral):
            return t.Int(const=True)
        if isinstance(node, ast.FloatLiteral):
            return t.Float(const=True)
        if isinstance(node, ast.ImaginaryLiteral):
            return t.Complex(const=True)
        if isinstance(node, ast.BooleanLiteral):
            return t.Bool(const=True)
        if isinstance(node, ast.BitstringLiteral):
            return t.Bit(node.width, const=True)
        if isinstance(node, ast.DurationLiteral):
            return t.Duration(const=True)
        if isinstance(node, ast.HardwareQubit):
            if self.inside("gate"):
                self.error("hardware qubits cannot be used inside a gate definition", node)
            return t.Qubit()
        if isinstance(node, ast.Identifier):
            return self.identifier_type(node)
        if isinstance(node, ast.UnaryExpression):
            return self.unary_type(node)
        if isinstance(node, ast.BinaryExpression):
            return self.binary_type(node)
        if isinstance(node, ast.Cast):
            return self.cast_type(node)
        if isinstance(node, ast.FunctionCall):
            return self.call_type(node)
        if isinstance(node, ast.IndexExpression):
            return self.index_type(self._type_of(node.collection), node.index, node)
        if isinstance(node, ast.IndexedIdentifier):
            type_ = self.identifier_type(node.name)
            for index in node.indices:
                type_ = self.index_type(type_, index, node)
            return type_
        if isinstance(node, ast.Concatenation):
            return self.concatenation_type(node)
        if isinstance(node, ast.DurationOf):
            with self.new_scope("block"):
                for statement in node.body:
                    self.statement(statement)
            return t.Duration()
        if isinstance(node, ast.MeasureExpression):
            operand = self.operand_type(node.operand)
            return t.Bit(operand.size if isinstance(operand, t.Qubit) else None)
        if isinstance(node, ast.QuantumCall):
            self.quantum_call(node)
            return t.Unknown()
        if isinstance(node, ast.ArrayLiteral):
            for value in node.values:
                self._type_of(value)
            return t.Unknown()
        return t.Unknown()

    def identifier_type(self, node: ast.Identifier) -> t.Type:
        symbol = self.resolve(node)
        if symbol is None:
            return t.Unknown()
        if symbol.kind in _GATE_KINDS:
            self.error(f"'{node.name}' is a gate, not a value", node)
            return t.Unknown()
        if symbol.kind in _CALLABLE_KINDS:
            self.error(f"'{node.name}' is a {symbol.kind.value}, not a value", node)
            return t.Unknown()
        return symbol.type

    def classical(self, type_: t.Type, node: ast.Node, what: str = "expressions") -> bool:
        if isinstance(type_, t.Qubit):
            self.error(f"qubits cannot be used in {what}", node)
            return False
        if isinstance(type_, t.Array):
            self.error(f"arrays cannot be used in {what}; index them first", node)
            return False
        return True

    def unary_type(self, node: ast.UnaryExpression) -> t.Type:
        operand = self._type_of(node.operand)
        if not self.classical(operand, node.operand):
            return t.Unknown()
        const = operand.const
        if node.op is ast.UnaryOperator.LOGICAL_NOT:
            return t.Bool(const=const)
        if node.op is ast.UnaryOperator.BIT_NOT:
            if isinstance(operand, (t.Float, t.Complex, t.Duration, t.Stretch)):
                self.error(f"'~' is not defined for {operand.describe()}", node)
                return t.Unknown()
            return operand
        if isinstance(operand, (t.Bool, t.Bit)):
            return t.Int(const=const)
        return operand

    def binary_type(self, node: ast.BinaryExpression) -> t.Type:
        lhs = self._type_of(node.lhs)
        rhs = self._type_of(node.rhs)
        if not (self.classical(lhs, node.lhs) and self.classical(rhs, node.rhs)):
            return t.Unknown()
        const = lhs.const and rhs.const
        op = node.op
        if op in (
            ast.BinaryOperator.LESS,
            ast.BinaryOperator.LESS_EQUAL,
            ast.BinaryOperator.GREATER,
            ast.BinaryOperator.GREATER_EQUAL,
            ast.BinaryOperator.EQUAL,
            ast.BinaryOperator.NOT_EQUAL,
            ast.BinaryOperator.LOGICAL_AND,
            ast.BinaryOperator.LOGICAL_OR,
        ):
            return t.Bool(const=const)
        if isinstance(lhs, t.Unknown) or isinstance(rhs, t.Unknown):
            return t.Unknown()
        timing = (t.Duration, t.Stretch)
        if isinstance(lhs, timing) or isinstance(rhs, timing):
            return self.timing_type(node, lhs, rhs, const)
        if op in (
            ast.BinaryOperator.SHIFT_LEFT,
            ast.BinaryOperator.SHIFT_RIGHT,
            ast.BinaryOperator.BIT_AND,
            ast.BinaryOperator.BIT_OR,
            ast.BinaryOperator.BIT_XOR,
        ):
            for side, type_ in ((node.lhs, lhs), (node.rhs, rhs)):
                if isinstance(type_, (t.Float, t.Complex)):
                    self.error(f"'{op.value}' is not defined for {type_.describe()}", side)
                    return t.Unknown()
            return lhs.as_const(const)
        return _arithmetic_result(lhs, rhs).as_const(const)

    def timing_type(
        self, node: ast.BinaryExpression, lhs: t.Type, rhs: t.Type, const: bool
    ) -> t.Type:
        timing = (t.Duration, t.Stretch)
        op = node.op
        both = isinstance(lhs, timing) and isinstance(rhs, timing)
        if op in (ast.BinaryOperator.ADD, ast.BinaryOperator.SUBTRACT):
            if not both:
                verb = "add" if op is ast.BinaryOperator.ADD else "subtract"
                other = rhs if isinstance(lhs, timing) else lhs
                self.error(f"cannot {verb} duration and {other.describe()}", node)
                return t.Unknown()
            return t.Duration(const=const)
        if op is ast.BinaryOperator.MULTIPLY and not both:
            return t.Duration(const=const)
        if op is ast.BinaryOperator.DIVIDE:
            if both:
                return t.Float(const=const)
            if isinstance(lhs, timing):
                return t.Duration(const=const)
        self.error(
            f"'{op.value}' is not defined between {lhs.describe()} and {rhs.describe()}", node
        )
        return t.Unknown()

    def concatenation_type(self, node: ast.Concatenation) -> t.Type:
        lhs = self._type_of(node.lhs)
        rhs = self._type_of(node.rhs)
        if isinstance(lhs, t.Unknown) or isinstance(rhs, t.Unknown):
            return t.Unknown()
        for kind in (t.Qubit, t.Bit):
            if isinstance(lhs, kind) and isinstance(rhs, kind):
                sizes = [1 if s.size is None else s.size for s in (lhs, rhs)]  # type: ignore[attr-defined]
                total = t.UNKNOWN_SIZE if t.UNKNOWN_SIZE in sizes else sum(sizes)
                return kind(total)
        if isinstance(lhs, t.Array) and isinstance(rhs, t.Array):
            return lhs
        self.error(f"cannot concatenate {lhs.describe()} and {rhs.describe()}", node)
        return t.Unknown()

    def cast_type(self, node: ast.Cast) -> t.Type:
        target = self.resolve_type(node.type)
        operand = self._type_of(node.operand)
        if isinstance(operand, t.Qubit):
            self.error("qubits cannot be cast to classical types", node.operand)
            return t.Unknown()
        if isinstance(operand, (t.Duration, t.Stretch)) and not isinstance(
            target, (t.Duration, t.Stretch)
        ):
            self.error(f"a duration cannot be cast to {target.describe()}", node)
        elif isinstance(target, t.Duration) and not isinstance(
            operand, (t.Duration, t.Stretch, t.Unknown)
        ):
            self.error(f"{operand.describe()} cannot be cast to duration", node)
        elif isinstance(operand, t.Angle) and isinstance(target, (t.Int, t.Float)):
            self.error(f"angle cannot be cast to {target.describe()}", node)
        elif isinstance(operand, t.Float) and isinstance(target, t.Bit):
            self.error("float cannot be cast to bit", node)
        return target.as_const(operand.const)

    def call_type(self, node: ast.FunctionCall) -> t.Type:
        symbol = self.resolve(node.name)
        arguments = node.arguments
        if symbol is None:
            for argument in arguments:
                self._type_of(argument)
            return t.Unknown()
        name = node.name.name
        if symbol.kind is SymbolKind.BUILTIN_FUNCTION:
            low, high, _ = self.functions[name]
            if not low <= len(arguments) <= high:
                count = str(low) if low == high else f"{low} or {high}"
                self.error(
                    f"'{name}' takes {count} argument{'s' if high > 1 else ''},"
                    f" but {_given(len(arguments))}",
                    node,
                )
            if name == "sizeof":
                for argument in arguments:
                    self._type_of(argument)
                return t.Int(signed=False, const=True)
            types = [self._type_of(a) for a in arguments]
            for argument, type_ in zip(arguments, types, strict=False):
                self.classical(type_, argument, "function arguments")
            return _builtin_result(name, types)
        if symbol.kind in _GATE_KINDS:
            self.error(f"'{name}' is a gate; apply it to qubits instead of calling it", node)
            return t.Unknown()
        if symbol.kind not in (SymbolKind.SUBROUTINE, SymbolKind.EXTERN):
            self.error(f"'{name}' is a {symbol.kind.value}, not a subroutine", node)
            return t.Unknown()
        signature = symbol.signature
        assert isinstance(signature, SubroutineSignature)
        if len(arguments) != len(signature.arguments):
            self.error(
                f"'{name}' takes {_plural(len(signature.arguments), 'argument')},"
                f" but {_given(len(arguments))}",
                node,
            )
        for argument, expected in zip(arguments, signature.arguments, strict=False):
            actual = self._type_of(argument)
            if isinstance(expected, t.Qubit):
                if not isinstance(actual, (t.Qubit, t.Unknown)):
                    self.error(
                        f"argument of '{name}' must be a qubit, not {actual.describe()}", argument
                    )
                elif (
                    isinstance(actual, t.Qubit)
                    and expected.size not in (None, t.UNKNOWN_SIZE)
                    and (actual.size or 1) not in (expected.size, t.UNKNOWN_SIZE)
                ):
                    self.error(
                        f"'{name}' expects qubit[{expected.size}], not {actual.describe()}",
                        argument,
                    )
            elif isinstance(actual, t.Qubit):
                self.error(
                    f"argument of '{name}' must be {expected.describe()}, not a qubit", argument
                )
            elif not _assignable(expected, actual):
                self.error(
                    f"argument of '{name}' must be {expected.describe()}, not {actual.describe()}",
                    argument,
                )
        return signature.return_type

    def index_type(self, type_: t.Type, index: ast.Index, node: ast.Node) -> t.Type:
        if isinstance(index, ast.DiscreteSet):
            for value in index.values:
                self.check_index(value, type_, node)
            if isinstance(type_, t.Array):
                dims = (len(index.values),) + type_.dimensions[1:]
                return t.Array(base=type_.base, dimensions=dims)
            return _selection(type_, len(index.values))
        if isinstance(type_, t.Unknown):
            for element in index:
                self.index_element_type(element)
            return t.Unknown()
        if isinstance(type_, t.Array):
            if len(index) > len(type_.dimensions):
                self.error(f"too many indices for {type_.describe()}", node)
                return t.Unknown()
            # Each scalar index removes a dimension; a range keeps it with its new length.
            kept = []
            for element, dimension in zip(index, type_.dimensions, strict=False):
                count = self.check_index(element, t.Bit(dimension), node)
                if count is not None:
                    kept.append(count)
            dims = tuple(kept) + tuple(type_.dimensions[len(index) :])
            return t.Array(base=type_.base, dimensions=dims) if dims else type_.base
        if len(index) != 1:
            self.error(f"{type_.describe()} has a single dimension", node)
            return t.Unknown()
        element = index[0]
        if isinstance(type_, (t.Qubit, t.Bit)):
            if type_.size is None:
                self.error(f"a single {type_.describe()} cannot be indexed", node)
                return t.Unknown()
            count = self.check_index(element, type_, node)
            return _selection(type_, count)
        if isinstance(type_, (t.Int, t.Angle)):
            # Bit-level access to integers and angles.
            count = self.check_index(
                element, t.Bit(type_.width if type_.width is not None else t.UNKNOWN_SIZE), node
            )
            return t.Bit(count) if isinstance(element, ast.Range) else t.Bit()
        self.error(f"{type_.describe()} cannot be indexed", node)
        return t.Unknown()

    def index_element_type(self, element: ast.IndexElement) -> None:
        if isinstance(element, ast.Range):
            for part in (element.start, element.end, element.step):
                if part is not None:
                    self._type_of(part)
        else:
            self._type_of(element)

    def range_length(self, element: ast.Range, size: int | None) -> int:
        """How many elements ``element`` selects from a register of ``size`` elements."""
        known = size is not None and size != t.UNKNOWN_SIZE
        start = 0 if element.start is None else self.evaluate(element.start)
        step = 1 if element.step is None else self.evaluate(element.step)
        if element.end is None:
            end = size - 1 if known and size is not None else None
        else:
            end = self.evaluate(element.end)
        if not (_is_integer(start) and _is_integer(step) and _is_integer(end)) or step == 0:
            return t.UNKNOWN_SIZE
        if known and size is not None:
            start = start + size if start < 0 else start
            end = end + size if end < 0 else end
        # Ranges include their end point.
        return max(0, (end - start) // step + 1)

    def check_index(self, element: ast.IndexElement, type_: t.Type, node: ast.Node) -> int | None:
        """Check an index against a register; return how many elements a range selects."""
        size = getattr(type_, "size", None)
        if isinstance(element, ast.Range):
            self.index_element_type(element)
            for part in (element.start, element.end):
                if part is not None:
                    self.check_bound(part, size)
            return self.range_length(element, size)
        index_type = self._type_of(element)
        if isinstance(index_type, (t.Float, t.Complex, t.Angle, t.Duration, t.Qubit)):
            self.error(f"an index must be an integer, not {index_type.describe()}", element)
            return None
        self.check_bound(element, size)
        return None

    def check_bound(self, element: ast.Expression, size: int | None) -> None:
        if size in (None, t.UNKNOWN_SIZE):
            return
        value = self.evaluate(element)
        if isinstance(value, Bits) or not isinstance(value, int) or isinstance(value, bool):
            return
        if not -size <= value < size:  # type: ignore[operator]
            self.error(f"index {value} is out of range for a register of size {size}", element)

    # -- quantum operands -----------------------------------------------------

    def operand_type(self, node: ast.Node) -> t.Type:
        """Check a quantum operand and return its (qubit) type."""
        if self.inside("gate") and isinstance(node, ast.IndexedIdentifier):
            self.error("qubit arguments cannot be indexed inside a gate definition", node)
            return t.Qubit()
        type_ = self._type_of(node)
        if not isinstance(type_, (t.Qubit, t.Unknown)):
            self.error(f"expected a qubit, found {type_.describe()}", node)
            return t.Unknown()
        return type_

    def operands(
        self, operands: Sequence[ast.Operand], what: str, distinct: bool, broadcast: bool = True
    ) -> list[t.Type]:
        types = [self.operand_type(operand) for operand in operands]
        sizes = {
            type_.size
            for type_ in types
            if isinstance(type_, t.Qubit) and type_.size not in (None, t.UNKNOWN_SIZE)
        }
        if broadcast and len(sizes) > 1:
            self.error(
                f"{what}: registers of different sizes"
                f" ({', '.join(map(str, sorted(sizes)))}) cannot be broadcast together",
                operands[0],
            )
        if distinct:
            seen: dict[tuple, ast.Node] = {}
            for operand in operands:
                key = self.operand_key(operand)
                if key is None:
                    continue
                if key in seen:
                    self.error(f"{what}: the same qubit is used more than once", operand)
                    break
                seen[key] = operand
        return types

    def operand_key(self, operand: ast.Operand) -> tuple | None:
        """A hashable identity for a single-qubit operand, if it is known statically."""
        if isinstance(operand, ast.HardwareQubit):
            return ("$", operand.index)
        if isinstance(operand, ast.Identifier):
            symbol, _ = self.lookup(operand.name)
            if symbol is not None and isinstance(symbol.type, t.Qubit) and symbol.type.size is None:
                return (operand.name,)
            return None
        if isinstance(operand, ast.IndexedIdentifier) and len(operand.indices) == 1:
            index = operand.indices[0]
            if isinstance(index, list) and len(index) == 1 and not isinstance(index[0], ast.Range):
                value = self.evaluate(index[0])
                if isinstance(value, int) and not isinstance(value, bool):
                    symbol, _ = self.lookup(operand.name.name)
                    size = getattr(symbol.type, "size", None) if symbol else None
                    if value < 0 and isinstance(size, int) and size > 0:
                        value += size
                    return (operand.name.name, value)
        return None

    # -- statements -------------------------------------------------------------

    def run(self) -> None:
        for statement in self.program.statements:
            self.statement(statement)

    def statements(self, statements: Sequence[ast.Statement], kind: str = "block") -> None:
        with self.new_scope(kind):
            for statement in statements:
                self.statement(statement)

    def statement(self, node: ast.Statement) -> None:
        method = getattr(self, f"statement_{type(node).__name__}", None)
        if method is not None:
            method(node)

    def statement_Include(self, node: ast.Include) -> None:
        name = node.filename
        if name in _LIBRARIES:
            if name in self.includes:
                return
            self.includes.append(name)
            program = parse(_library_source(name), filename=name, ignore_version=True)
            self._in_library = True
            try:
                for statement in program.statements:
                    self.statement(statement)
                if name == "qelib1.inc":
                    for symbol in self.globals:
                        if symbol.kind is SymbolKind.GATE and symbol.name not in _SPEC_QELIB1_GATES:
                            symbol.overridable = True
            finally:
                self._in_library = False
            return
        path = self.find_include(name)
        if path is None:
            self.error(f"cannot find the included file '{name}'", node, QasmIncludeError)
            return
        absolute = os.path.abspath(path)
        if absolute in self._include_stack:
            self.error(f"'{name}' includes itself", node, QasmIncludeError)
            return
        try:
            with open(path, encoding="utf-8") as file:
                source = file.read()
            program = parse(source, filename=path, ignore_version=True)
        except OSError as exc:
            self.error(
                f"cannot read the included file '{name}': {exc.strerror}", node, QasmIncludeError
            )
            return
        except QasmError as exc:
            self.errors.append(exc)
            return
        self.includes.append(path)
        self._include_stack.append(absolute)
        self._file_stack.append(path)
        try:
            for statement in program.statements:
                self.statement(statement)
        finally:
            self._include_stack.pop()
            self._file_stack.pop()

    def find_include(self, name: str) -> str | None:
        if os.path.isabs(name):
            return name if os.path.isfile(name) else None
        directories = []
        current = self._file_stack[-1]
        if current:
            directories.append(os.path.dirname(os.path.abspath(current)))
        directories.extend(self.include_paths)
        directories.append(os.getcwd())
        for directory in directories:
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate):
                return candidate
        return None

    def statement_Block(self, node: ast.Block) -> None:
        self.statements(node.statements)

    def statement_ExpressionStatement(self, node: ast.ExpressionStatement) -> None:
        self._type_of(node.expression)

    def statement_QubitDeclaration(self, node: ast.QubitDeclaration) -> None:
        size = self.size(node.size, "the size of a qubit register")
        self.declare(
            Symbol(node.name.name, SymbolKind.QUBIT, t.Qubit(size), node.name.span), node.name
        )

    def statement_ClassicalDeclaration(self, node: ast.ClassicalDeclaration) -> None:
        type_ = self.resolve_type(node.type)
        if node.init is not None:
            self.check_initialiser(type_, node.init, node.name.name)
        self.declare(Symbol(node.name.name, SymbolKind.VARIABLE, type_, node.name.span), node.name)

    def check_initialiser(self, type_: t.Type, init: ast.Node, name: str) -> None:
        if isinstance(init, ast.ArrayLiteral):
            if not isinstance(type_, t.Array):
                self.error(f"an array literal cannot initialise {type_.describe()} '{name}'", init)
                return
            self.check_array_literal(init, type_.dimensions, type_.base)
            return
        if isinstance(init, (ast.MeasureExpression, ast.QuantumCall)):
            self.check_measure_target(type_, init, init)
            return
        value = self._type_of(init)
        if not _assignable(type_, value):
            self.error(
                f"cannot initialise {type_.describe()} '{name}'"
                f" with a value of type {value.describe()}",
                init,
            )

    def check_array_literal(
        self, node: ast.ArrayLiteral, dims: tuple[int, ...], base: t.Type
    ) -> None:
        if not dims:
            self.error("too many nested values in array literal", node)
            return
        if dims[0] != t.UNKNOWN_SIZE and len(node.values) != dims[0]:
            self.error(
                f"expected {dims[0]} values in array literal, found {len(node.values)}", node
            )
        for value in node.values:
            if isinstance(value, ast.ArrayLiteral):
                self.check_array_literal(value, dims[1:], base)
                continue
            value_type = self._type_of(value)
            if len(dims) > 1 and not isinstance(value_type, (t.Array, t.Unknown)):
                self.error(f"expected a nested array literal of {dims[1]} values", value)
            elif len(dims) == 1 and not _assignable(base, value_type):
                self.error(
                    f"array element must be {base.describe()}, not {value_type.describe()}", value
                )

    def statement_ConstDeclaration(self, node: ast.ConstDeclaration) -> None:
        type_ = self.resolve_type(node.type).as_const()
        value = None
        if isinstance(node.init, (ast.MeasureExpression, ast.QuantumCall)):
            self.error("a constant cannot be initialised with a measurement", node.init)
        else:
            init_type = self._type_of(node.init)
            if not _assignable(type_, init_type):
                self.error(
                    f"cannot initialise {type_.describe()} '{node.name.name}'"
                    f" with a value of type {init_type.describe()}",
                    node.init,
                )
            try:
                value = _convert(type_, self.evaluator(node.init))
            except NotConstant as exc:
                name = getattr(exc.node, "name", None)
                reason = f"'{name}' is not a constant" if name else "it depends on run-time values"
                self.error(
                    f"the value of constant '{node.name.name}' must be a constant"
                    f" expression: {reason}",
                    node.init,
                )
            except CannotEvaluate:
                value = None
        self.declare(
            Symbol(node.name.name, SymbolKind.CONSTANT, type_, node.name.span, value=value),
            node.name,
        )

    def statement_IODeclaration(self, node: ast.IODeclaration) -> None:
        kind = SymbolKind.INPUT if node.io is ast.IOKeyword.INPUT else SymbolKind.OUTPUT
        self.declare(
            Symbol(node.name.name, kind, self.resolve_type(node.type), node.name.span), node.name
        )

    def statement_AliasDeclaration(self, node: ast.AliasDeclaration) -> None:
        type_ = self._type_of(node.value)
        if not isinstance(type_, (t.Qubit, t.Bit, t.Array, t.Unknown)):
            self.error(
                f"'let' can only alias qubits, bit registers or arrays, not {type_.describe()}",
                node.value,
            )
            type_ = t.Unknown()
        self.declare(Symbol(node.name.name, SymbolKind.ALIAS, type_, node.name.span), node.name)

    def assignment_target(self, target: ast.Assignable) -> t.Type | None:
        name = target.name if isinstance(target, ast.IndexedIdentifier) else target
        symbol = self.resolve(name)
        if symbol is None:
            return None
        if symbol.kind in (SymbolKind.CONSTANT, SymbolKind.BUILTIN_CONSTANT):
            self.error(f"cannot assign to the constant '{name.name}'", target)
            return None
        if symbol.kind in _GATE_KINDS | _CALLABLE_KINDS:
            self.error(f"cannot assign to the {symbol.kind.value} '{name.name}'", target)
            return None
        if symbol.kind is SymbolKind.INPUT:
            self.error(f"cannot assign to the input '{name.name}'", target)
            return None
        if isinstance(symbol.type, t.Qubit):
            self.error(f"cannot assign to the qubit '{name.name}'", target)
            return None
        if (
            isinstance(symbol.type, t.Array)
            and symbol.kind is SymbolKind.ARGUMENT
            and symbol.value == "readonly"
        ):
            self.error(f"cannot assign to the read-only array '{name.name}'", target)
            return None
        type_ = symbol.type
        if isinstance(target, ast.IndexedIdentifier):
            for index in target.indices:
                type_ = self.index_type(type_, index, target)
        return type_

    def statement_Assignment(self, node: ast.Assignment) -> None:
        target = self.assignment_target(node.target)
        if isinstance(node.value, (ast.MeasureExpression, ast.QuantumCall)):
            if target is not None:
                self.check_measure_target(target, node.value, node.target)
            else:
                self._type_of(node.value)
            return
        value = self._type_of(node.value)
        if target is None:
            return
        if node.op is ast.AssignmentOperator.ASSIGN:
            if not _assignable(target, value):
                self.error(
                    f"cannot assign a value of type {value.describe()} to {target.describe()}",
                    node.value,
                )
        elif self.classical(value, node.value):
            self.compound_assignment(node.op, target, value, node)

    def compound_assignment(
        self, op: ast.AssignmentOperator, target: t.Type, value: t.Type, node: ast.Node
    ) -> None:
        if isinstance(target, t.Unknown) or isinstance(value, t.Unknown):
            return
        timing = (t.Duration, t.Stretch)
        if isinstance(target, timing) != isinstance(value, timing) and op in (
            ast.AssignmentOperator.ADD,
            ast.AssignmentOperator.SUBTRACT,
        ):
            self.error(
                f"cannot combine {target.describe()} and {value.describe()} with '{op.value}'", node
            )

    def check_measure_target(self, target: t.Type, measure: ast.Node, where: ast.Node) -> None:
        result = self._type_of(measure)
        if isinstance(measure, ast.QuantumCall) or isinstance(target, t.Unknown):
            return
        if not isinstance(target, (t.Bit, t.Bool)):
            self.error(f"a measurement result cannot be stored in {target.describe()}", where)
            return
        if isinstance(target, t.Bool):
            target = t.Bit()
        sizes = {
            (s.size if s.size is not None else 1) for s in (target, result) if isinstance(s, t.Bit)
        }  # type: ignore[union-attr]
        sizes.discard(t.UNKNOWN_SIZE)
        if len(sizes) > 1:
            self.error(
                f"cannot store the measurement of {_count(result)} in {target.describe()}", where
            )

    def statement_MeasureStatement(self, node: ast.MeasureStatement) -> None:
        if node.target is None:
            self._type_of(node.measure)
            return
        target = self.assignment_target(node.target)
        if target is None:
            self._type_of(node.measure)
            return
        self.check_measure_target(target, node.measure, node.target)

    def quantum_call(self, node: ast.QuantumCall) -> None:
        if node.name.name not in self.calibrated_gates:
            symbol, _ = self.lookup(node.name.name)
            if symbol is None:
                self.error(f"'{node.name.name}' has no 'defcal' definition", node.name)
        for argument in node.arguments:
            self._type_of(argument)
        self.operands(node.operands, f"'{node.name.name}'", distinct=False)

    def statement_GateDefinition(self, node: ast.GateDefinition) -> None:
        name = node.name.name
        signature = GateSignature(len(node.parameters), len(node.qubits))
        self.declare(
            Symbol(name, SymbolKind.GATE, span=node.name.span, signature=signature), node.name
        )
        previous_gate, self.current_gate = self.current_gate, name
        with self.new_scope("gate"):
            for parameter in node.parameters:
                self.declare(
                    Symbol(parameter.name, SymbolKind.GATE_PARAMETER, t.Angle(), parameter.span),
                    parameter,
                )
            for qubit in node.qubits:
                self.declare(
                    Symbol(qubit.name, SymbolKind.GATE_QUBIT, t.Qubit(), qubit.span), qubit
                )
            for statement in node.body:
                self.statement(statement)
        self.current_gate = previous_gate

    def statement_OpaqueDefinition(self, node: ast.OpaqueDefinition) -> None:
        signature = GateSignature(len(node.parameters), len(node.qubits))
        self.declare(
            Symbol(node.name.name, SymbolKind.GATE, span=node.name.span, signature=signature),
            node.name,
        )
        seen: set[str] = set()
        for identifier in [*node.parameters, *node.qubits]:
            if identifier.name in seen:
                self.error(f"'{identifier.name}' is already declared in this scope", identifier)
            seen.add(identifier.name)

    def control_count(self, modifiers: Sequence[ast.GateModifier]) -> int | None:
        """Number of control qubits the modifiers add (``None`` if not computable)."""
        total: int | None = 0
        for modifier in modifiers:
            if modifier.kind is ast.GateModifierKind.POW:
                type_ = self._type_of(modifier.argument)  # type: ignore[arg-type]
                self.classical(type_, modifier.argument)  # type: ignore[arg-type]
                continue
            if modifier.kind is ast.GateModifierKind.INV:
                continue
            count: int | None = 1
            if modifier.argument is not None:
                count = self.size(
                    modifier.argument, f"the number of qubits of '{modifier.kind.value}'"
                )
                if count == t.UNKNOWN_SIZE:
                    count = None
            total = None if total is None or count is None else total + count
        return total

    def statement_GateCall(self, node: ast.GateCall) -> None:
        name = node.name.name
        symbol, _ = self.lookup(name)
        if symbol is not None and symbol.kind in (SymbolKind.SUBROUTINE, SymbolKind.EXTERN):
            self.subroutine_with_gate_syntax(node, symbol)
            return
        controls = self.control_count(node.modifiers)
        for argument in node.arguments:
            type_ = self._type_of(argument)
            if self.classical(type_, argument, "gate parameters") and isinstance(
                type_, (t.Duration, t.Stretch, t.Complex)
            ):
                self.error(f"a gate parameter must be an angle, not {type_.describe()}", argument)
        if node.duration is not None:
            self.check_duration(node.duration)
        self.operands(node.qubits, f"gate '{name}'", distinct=True)
        if self.current_gate is not None and name == self.current_gate:
            self.error(f"gate '{name}' cannot call itself", node.name)
            return
        if symbol is None:
            if name not in self.calibrated_gates:
                self.error(f"undefined gate '{name}'", node.name)
            return
        if symbol.kind not in _GATE_KINDS:
            self.error(f"'{name}' is a {symbol.kind.value}, not a gate", node.name)
            return
        signature = symbol.signature
        assert isinstance(signature, GateSignature)
        if len(node.arguments) != signature.parameters:
            self.error(
                f"gate '{name}' takes {_plural(signature.parameters, 'parameter')},"
                f" but {_given(len(node.arguments))}",
                node,
            )
        if controls is not None:
            expected = signature.qubits + controls
            if len(node.qubits) != expected:
                self.error(
                    f"gate '{name}' acts on {_plural(expected, 'qubit')},"
                    f" but {_given(len(node.qubits))}",
                    node,
                )

    def subroutine_with_gate_syntax(self, node: ast.GateCall, symbol: Symbol) -> None:
        """``f(a) q0, q1;``: a subroutine called with the gate syntax of earlier
        drafts of the specification (still used by its examples)."""
        name = node.name.name
        if node.modifiers or node.duration is not None:
            self.error(
                f"'{name}' is a {symbol.kind.value}; modifiers and durations apply only to gates",
                node,
            )
        signature = symbol.signature
        assert isinstance(signature, SubroutineSignature)
        given = len(node.arguments) + len(node.qubits)
        if given != len(signature.arguments):
            self.error(
                f"'{name}' takes {_plural(len(signature.arguments), 'argument')},"
                f" but {_given(given)}",
                node,
            )
        for argument, expected in zip(
            [*node.arguments, *node.qubits], signature.arguments, strict=False
        ):
            actual = self._type_of(argument)
            if isinstance(expected, t.Qubit) != isinstance(actual, t.Qubit) and not isinstance(
                actual, t.Unknown
            ):
                wanted = "a qubit" if isinstance(expected, t.Qubit) else expected.describe()
                self.error(
                    f"argument of '{name}' must be {wanted}, not {actual.describe()}", argument
                )
        self.operands(node.qubits, f"'{name}'", distinct=True, broadcast=False)

    def statement_GlobalPhase(self, node: ast.GlobalPhase) -> None:
        controls = self.control_count(node.modifiers)
        type_ = self._type_of(node.argument)
        self.classical(type_, node.argument, "gate parameters")
        if node.duration is not None:
            self.check_duration(node.duration)
        self.operands(node.qubits, "gphase", distinct=True)
        if controls is not None and len(node.qubits) != controls:
            self.error(
                f"gphase acts on {controls} control qubit{'s' if controls != 1 else ''},"
                f" but {_given(len(node.qubits))}",
                node,
            )

    def check_duration(self, node: ast.Expression) -> None:
        type_ = self._type_of(node)
        if not isinstance(type_, (t.Duration, t.Stretch, t.Unknown)):
            self.error(f"expected a duration, found {type_.describe()}", node)

    def statement_Reset(self, node: ast.Reset) -> None:
        self.operands([node.operand], "reset", distinct=False)

    def statement_Barrier(self, node: ast.Barrier) -> None:
        self.operands(node.operands, "barrier", distinct=False)

    def statement_Nop(self, node: ast.Nop) -> None:
        self.operands(node.operands, "nop", distinct=False)

    def statement_Delay(self, node: ast.Delay) -> None:
        self.check_duration(node.duration)
        self.operands(node.operands, "delay", distinct=False)

    def statement_Box(self, node: ast.Box) -> None:
        if node.duration is not None:
            self.check_duration(node.duration)
        self.statements(node.body)

    def argument_type(self, argument: ast.ClassicalArgument | ast.QuantumArgument) -> t.Type:
        if isinstance(argument, ast.QuantumArgument):
            return t.Qubit(self.size(argument.size, "the size of a qubit register"))
        return self.resolve_type(argument.type)

    def statement_SubroutineDefinition(self, node: ast.SubroutineDefinition) -> None:
        argument_types = tuple(self.argument_type(a) for a in node.arguments)
        return_type = self.resolve_type(node.return_type) if node.return_type else t.Void()
        signature = SubroutineSignature(argument_types, return_type)
        self.declare(
            Symbol(node.name.name, SymbolKind.SUBROUTINE, span=node.name.span, signature=signature),
            node.name,
        )
        self.return_types.append(return_type)
        with self.new_scope("subroutine"):
            for argument, type_ in zip(node.arguments, argument_types, strict=False):
                access = (
                    getattr(argument.type, "access", None)
                    if isinstance(argument, ast.ClassicalArgument)
                    else None
                )
                symbol = Symbol(argument.name.name, SymbolKind.ARGUMENT, type_, argument.name.span)
                if access is not None:
                    symbol.value = access.value
                self.declare(symbol, argument.name)
            for statement in node.body:
                self.statement(statement)
        self.return_types.pop()

    def statement_ExternDeclaration(self, node: ast.ExternDeclaration) -> None:
        argument_types = tuple(self.resolve_type(a.type) for a in node.arguments)
        return_type = self.resolve_type(node.return_type) if node.return_type else t.Void()
        signature = SubroutineSignature(argument_types, return_type)
        self.declare(
            Symbol(node.name.name, SymbolKind.EXTERN, span=node.name.span, signature=signature),
            node.name,
        )

    def statement_Return(self, node: ast.Return) -> None:
        expected = self.return_types[-1] if self.return_types else None
        if node.value is None:
            if expected is not None and not isinstance(expected, t.Void):
                self.error(
                    f"this subroutine must return a value of type {expected.describe()}", node
                )
            return
        value = self._type_of(node.value)
        if expected is None:
            return
        if isinstance(expected, t.Void):
            self.error("this subroutine does not return a value", node.value)
        elif not _assignable(expected, value):
            self.error(
                f"cannot return {value.describe()} from a subroutine returning"
                f" {expected.describe()}",
                node.value,
            )

    def condition(self, node: ast.Expression) -> None:
        type_ = self._type_of(node)
        if not self.classical(type_, node, "conditions"):
            return
        if isinstance(type_, (t.Duration, t.Stretch, t.Complex)):
            self.error(f"a condition must be a boolean value, not {type_.describe()}", node)

    def statement_If(self, node: ast.If) -> None:
        self.condition(node.condition)
        self.statements(node.then_body)
        self.statements(node.else_body)

    def statement_While(self, node: ast.While) -> None:
        self.condition(node.condition)
        self.statements(node.body)

    def statement_For(self, node: ast.For) -> None:
        loop_type = self.resolve_type(node.type)
        iterable = node.iterable
        if isinstance(iterable, ast.Range):
            for part in (iterable.start, iterable.end, iterable.step):
                if part is not None:
                    self.classical(self._type_of(part), part, "ranges")
            if iterable.start is None or iterable.end is None:
                self.error("a loop range needs a start and an end", iterable)
        elif isinstance(iterable, ast.DiscreteSet):
            for value in iterable.values:
                self.classical(self._type_of(value), value, "loop sets")
        else:
            type_ = self._type_of(iterable)
            if isinstance(type_, t.Qubit):
                self.error("cannot loop over qubits", iterable)
            elif not isinstance(type_, (t.Array, t.Bit, t.Unknown)) or (
                isinstance(type_, t.Bit) and type_.size is None
            ):
                self.error(f"cannot loop over {type_.describe()}", iterable)
        with self.new_scope("block"):
            self.declare(
                Symbol(node.variable.name, SymbolKind.LOOP_VARIABLE, loop_type, node.variable.span),
                node.variable,
            )
            for statement in node.body:
                self.statement(statement)

    def statement_Switch(self, node: ast.Switch) -> None:
        type_ = self._type_of(node.target)
        if self.classical(type_, node.target, "switch statements") and not isinstance(
            type_, (t.Int, t.Bit, t.Bool, t.Unknown)
        ):
            self.error(f"switch needs an integer value, not {type_.describe()}", node.target)
        seen: dict[int, ast.Node] = {}
        for case in node.cases:
            for value in case.values:
                case_type = self._type_of(value)
                if isinstance(case_type, (t.Float, t.Complex, t.Angle, t.Duration)):
                    self.error(f"case values must be integers, not {case_type.describe()}", value)
                    continue
                try:
                    constant = self.evaluator.integer(value)
                except NotConstant:
                    self.error("case values must be constant expressions", value)
                    continue
                except CannotEvaluate:
                    continue
                if constant in seen:
                    self.error(f"duplicate case value {constant}", value)
                seen[constant] = value
            self.statements(case.body)
        if node.default is not None:
            self.statements(node.default)

    def statement_CalibrationDefinition(self, node: ast.CalibrationDefinition) -> None:
        name = node.name.name
        symbol, _ = self.lookup(name)
        if (
            symbol is not None
            and symbol.kind not in _GATE_KINDS
            and name not in ("measure", "reset", "delay")
        ):
            self.error(f"'{name}' is already declared as a {symbol.kind.value}", node.name)
        self.calibrated_gates.add(name)


def _selection(type_: t.Type, count: int | None) -> t.Type:
    """The type of ``count`` elements selected from a register (``None``: a single one)."""
    if isinstance(type_, (t.Qubit, t.Bit)):
        return type(type_)(count)
    return t.Unknown()


def _is_integer(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _given(count: int) -> str:
    return f"{count} {'was' if count == 1 else 'were'} given"


def _count(type_: t.Type) -> str:
    if isinstance(type_, t.Bit) and type_.size not in (None, t.UNKNOWN_SIZE):
        return f"{type_.size} qubits"
    return "a qubit"


def _rank(type_: t.Type) -> int:
    if isinstance(type_, t.Complex):
        return 4
    if isinstance(type_, t.Float):
        return 3
    if isinstance(type_, t.Angle):
        return 2
    return 1


def _arithmetic_result(lhs: t.Type, rhs: t.Type) -> t.Type:
    """The result type of an arithmetic operation, following the usual promotions."""
    if isinstance(lhs, t.Angle) and isinstance(rhs, t.Angle):
        return lhs
    if isinstance(lhs, t.Angle) and not isinstance(rhs, (t.Float, t.Complex)):
        return lhs
    if isinstance(rhs, t.Angle) and not isinstance(lhs, (t.Float, t.Complex)):
        return rhs
    winner = lhs if _rank(lhs) >= _rank(rhs) else rhs
    if isinstance(winner, (t.Bool, t.Bit)):
        return t.Int()
    return winner


def _builtin_result(name: str, arguments: list[t.Type]) -> t.Type:
    const = all(a.const for a in arguments)
    if name == "popcount":
        return t.Int(signed=False, const=const)
    if name in ("rotl", "rotr", "mod") and arguments:
        return arguments[0]
    if name in ("ceiling", "floor", "real", "imag", "arccos", "arcsin", "arctan", "ln"):
        return t.Float(const=const)
    if arguments and isinstance(arguments[0], t.Complex):
        return t.Complex(const=const)
    if name == "pow" and arguments and all(isinstance(a, t.Int) for a in arguments):
        return t.Int(const=const)
    return t.Float(const=const)


def _assignable(target: t.Type, value: t.Type) -> bool:
    """Whether ``value`` can be assigned (implicitly converted) to ``target``.

    Only clearly incompatible combinations are rejected.
    """
    if isinstance(target, (t.Unknown, t.Void)) or isinstance(value, t.Unknown):
        return True
    if isinstance(target, t.Qubit) or isinstance(value, (t.Qubit, t.Void)):
        return False
    if isinstance(target, t.Array) or isinstance(value, t.Array):
        if not (isinstance(target, t.Array) and isinstance(value, t.Array)):
            return False
        if len(target.dimensions) != len(value.dimensions):
            return False
        return all(
            a == b or t.UNKNOWN_SIZE in (a, b)
            for a, b in zip(target.dimensions, value.dimensions, strict=False)
        )
    timing = (t.Duration, t.Stretch)
    if isinstance(target, timing) or isinstance(value, timing):
        return isinstance(target, timing) and isinstance(value, timing)
    if isinstance(value, t.Complex):
        return isinstance(target, t.Complex)
    if isinstance(target, t.Bit) and target.size is not None:
        if isinstance(value, t.Bit):
            sizes = {target.size, 1 if value.size is None else value.size}
            return len(sizes) == 1 or t.UNKNOWN_SIZE in sizes
        return not isinstance(value, (t.Float, t.Angle))
    if isinstance(value, t.Angle):
        return isinstance(target, (t.Angle, t.Bool, t.Bit))
    return True


def _convert(type_: t.Type, value):
    """Convert a constant value to the declared type of a constant."""
    if isinstance(value, Bits) and isinstance(type_, t.Int):
        return value.value
    if isinstance(type_, t.Int) and isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if (
        isinstance(type_, (t.Float, t.Angle))
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        return float(value)
    if isinstance(type_, t.Bool):
        return bool(value.value if isinstance(value, Bits) else value)
    if isinstance(type_, t.Complex) and not isinstance(value, Bits):
        return complex(value)
    return value


def analyze(
    program: ast.Program,
    *,
    filename: str | None = None,
    include_paths: Sequence[str | os.PathLike] = (),
) -> Analysis:
    """Check a parsed program against the semantic rules of the language.

    Args:
        program: the parsed program.
        filename: the file the program was read from; ``include`` statements
            are resolved relative to its directory first.
        include_paths: further directories searched by ``include`` statements.
            ``stdgates.inc`` and ``qelib1.inc`` are always available.

    Returns:
        An :class:`Analysis`; ``analysis.errors`` lists every problem found.
    """
    analyzer = _Analyzer(program, filename or _program_file(program), include_paths)
    analyzer.run()
    return Analysis(
        program=program,
        errors=analyzer.errors,
        globals=analyzer.globals,
        includes=analyzer.includes,
        calibrated_gates=analyzer.calibrated_gates,
    )


def _program_file(program: ast.Program) -> str | None:
    return program.span.file if program.span is not None else None
