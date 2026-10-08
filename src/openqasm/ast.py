"""Abstract syntax tree for OpenQASM 3 programs.

Every node is a dataclass. Nodes compare equal when their content is equal;
source locations (``span``), comments and purely cosmetic choices such as the
OpenQASM 2 spelling of a declaration are ignored by comparisons, so a program
and its pretty-printed form parse to equal trees.

The tree mirrors the grammar of the specification closely, with a few
deliberate differences that make the tree easier to consume:

* hardware qubits (``$0``) are :class:`HardwareQubit` nodes, not identifiers;
* ``creg``/``qreg`` declarations become ordinary bit/qubit declarations that
  remember their original spelling in ``legacy_syntax``;
* ``c = measure q;`` and ``measure q -> c;`` are both a :class:`MeasureStatement`;
* every statement carries its ``annotations``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

__all__ = [
    # infrastructure
    "Span",
    "Node",
    "Comment",
    # enums
    "UnaryOperator",
    "BinaryOperator",
    "AssignmentOperator",
    "TimeUnit",
    "GateModifierKind",
    "IOKeyword",
    "AccessControl",
    # expressions
    "Expression",
    "Identifier",
    "HardwareQubit",
    "IntegerLiteral",
    "FloatLiteral",
    "ImaginaryLiteral",
    "BooleanLiteral",
    "BitstringLiteral",
    "DurationLiteral",
    "ArrayLiteral",
    "UnaryExpression",
    "BinaryExpression",
    "Cast",
    "FunctionCall",
    "IndexExpression",
    "Concatenation",
    "DurationOf",
    "Range",
    "DiscreteSet",
    "IndexedIdentifier",
    "MeasureExpression",
    "QuantumCall",
    # types
    "Type",
    "ScalarType",
    "IntType",
    "UintType",
    "FloatType",
    "AngleType",
    "BitType",
    "BoolType",
    "ComplexType",
    "DurationType",
    "StretchType",
    "ArrayType",
    "ArrayReferenceType",
    "QubitType",
    # statements
    "Statement",
    "Annotation",
    "Pragma",
    "Program",
    "Include",
    "CalibrationGrammar",
    "Block",
    "ExpressionStatement",
    "QubitDeclaration",
    "ClassicalDeclaration",
    "ConstDeclaration",
    "IODeclaration",
    "AliasDeclaration",
    "Assignment",
    "GateModifier",
    "GateDefinition",
    "OpaqueDefinition",
    "GateCall",
    "GlobalPhase",
    "MeasureStatement",
    "Reset",
    "Barrier",
    "Delay",
    "Nop",
    "Box",
    "ClassicalArgument",
    "QuantumArgument",
    "SubroutineDefinition",
    "ExternArgument",
    "ExternDeclaration",
    "Return",
    "Break",
    "Continue",
    "End",
    "If",
    "While",
    "For",
    "SwitchCase",
    "Switch",
    "CalibrationBlock",
    "CalibrationDefinition",
    # aliases
    "Operand",
    "IndexElement",
    "Index",
    "Assignable",
]


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Span:
    """A region of source text.

    Lines start at 1, columns at 0. The end position is exclusive, so a span
    always covers ``end_column - start_column`` characters on a single line.
    """

    start_line: int
    start_column: int
    end_line: int
    end_column: int
    file: str | None = None

    def merge(self, other: Span | None) -> Span:
        """The smallest span covering both ``self`` and ``other``."""
        if other is None:
            return self
        start = min((self.start_line, self.start_column), (other.start_line, other.start_column))
        end = max((self.end_line, self.end_column), (other.end_line, other.end_column))
        return Span(start[0], start[1], end[0], end[1], self.file)

    def __str__(self) -> str:
        prefix = f"{self.file}:" if self.file else ""
        return f"{prefix}{self.start_line}:{self.start_column + 1}"


@dataclass
class Node:
    """Base class of every node of the tree."""

    span: Span | None = field(default=None, compare=False, repr=False, kw_only=True)


@dataclass
class Comment(Node):
    """A ``//`` or ``/* */`` comment, kept by the parser in :attr:`Program.comments`."""

    text: str

    @property
    def is_block(self) -> bool:
        return self.text.startswith("/*")


# ---------------------------------------------------------------------------
# Operators and small enumerations
# ---------------------------------------------------------------------------


class UnaryOperator(enum.Enum):
    NEGATE = "-"
    BIT_NOT = "~"
    LOGICAL_NOT = "!"


class BinaryOperator(enum.Enum):
    POWER = "**"
    MULTIPLY = "*"
    DIVIDE = "/"
    MODULO = "%"
    ADD = "+"
    SUBTRACT = "-"
    SHIFT_LEFT = "<<"
    SHIFT_RIGHT = ">>"
    LESS = "<"
    LESS_EQUAL = "<="
    GREATER = ">"
    GREATER_EQUAL = ">="
    EQUAL = "=="
    NOT_EQUAL = "!="
    BIT_AND = "&"
    BIT_XOR = "^"
    BIT_OR = "|"
    LOGICAL_AND = "&&"
    LOGICAL_OR = "||"


class AssignmentOperator(enum.Enum):
    ASSIGN = "="
    ADD = "+="
    SUBTRACT = "-="
    MULTIPLY = "*="
    DIVIDE = "/="
    BIT_AND = "&="
    BIT_OR = "|="
    BIT_XOR = "^="
    SHIFT_LEFT = "<<="
    SHIFT_RIGHT = ">>="
    MODULO = "%="
    POWER = "**="

    @property
    def binary(self) -> BinaryOperator | None:
        """The binary operator a compound assignment applies (``None`` for ``=``)."""
        if self is AssignmentOperator.ASSIGN:
            return None
        return BinaryOperator(self.value[:-1])


class TimeUnit(enum.Enum):
    DT = "dt"
    NS = "ns"
    US = "us"
    MS = "ms"
    S = "s"

    @property
    def seconds(self) -> float | None:
        """Length of the unit in seconds (``None`` for the backend-defined ``dt``)."""
        return {"ns": 1e-9, "us": 1e-6, "ms": 1e-3, "s": 1.0}.get(self.value)


class GateModifierKind(enum.Enum):
    INV = "inv"
    POW = "pow"
    CTRL = "ctrl"
    NEGCTRL = "negctrl"


class IOKeyword(enum.Enum):
    INPUT = "input"
    OUTPUT = "output"


class AccessControl(enum.Enum):
    READONLY = "readonly"
    MUTABLE = "mutable"


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


@dataclass
class Expression(Node):
    """Base class of expressions."""


@dataclass
class Identifier(Expression):
    name: str


@dataclass
class HardwareQubit(Expression):
    """A physical qubit such as ``$3``."""

    index: int

    @property
    def name(self) -> str:
        return f"${self.index}"


@dataclass
class IntegerLiteral(Expression):
    value: int


@dataclass
class FloatLiteral(Expression):
    value: float


@dataclass
class ImaginaryLiteral(Expression):
    """An imaginary number such as ``1.5im``; ``value`` is the imaginary part."""

    value: float


@dataclass
class BooleanLiteral(Expression):
    value: bool


@dataclass
class BitstringLiteral(Expression):
    """A bit string such as ``"0110"``; ``value`` holds the bits as an integer."""

    value: int
    width: int

    @property
    def bits(self) -> str:
        return format(self.value, f"0{self.width}b") if self.width else ""


@dataclass
class DurationLiteral(Expression):
    value: float
    unit: TimeUnit


@dataclass
class ArrayLiteral(Expression):
    """``{1, 2, 3}`` or nested ``{{1, 2}, {3, 4}}``, used to initialise arrays."""

    values: list[Expression]


@dataclass
class UnaryExpression(Expression):
    op: UnaryOperator
    operand: Expression


@dataclass
class BinaryExpression(Expression):
    op: BinaryOperator
    lhs: Expression
    rhs: Expression


@dataclass
class Cast(Expression):
    type: ScalarType | ArrayType
    operand: Expression


@dataclass
class FunctionCall(Expression):
    """A call to a subroutine, an ``extern`` or a built-in function such as ``sin``."""

    name: Identifier
    arguments: list[Expression]


@dataclass
class Range(Node):
    """``start:end`` or ``start:step:end``; any part may be omitted (``[:]``)."""

    start: Expression | None = None
    end: Expression | None = None
    step: Expression | None = None


@dataclass
class DiscreteSet(Node):
    """``{1, 3, 5}`` used as an index or as the iterable of a ``for`` loop."""

    values: list[Expression]


IndexElement = Expression | Range
Index = DiscreteSet | list[IndexElement]


@dataclass
class IndexExpression(Expression):
    """``collection[index]``. A multi-dimensional index ``a[1, 2]`` has two elements."""

    collection: Expression
    index: Index


@dataclass
class Concatenation(Expression):
    """``lhs ++ rhs``, only valid on the right-hand side of a ``let``."""

    lhs: Expression
    rhs: Expression


@dataclass
class DurationOf(Expression):
    """``durationof({ ... })``."""

    body: list[Statement]


@dataclass
class IndexedIdentifier(Node):
    """An assignable or quantum operand with one or more indices: ``q[0]``, ``a[1][2:3]``."""

    name: Identifier
    indices: list[Index]


@dataclass
class MeasureExpression(Node):
    """``measure q[0]``. Not an expression: it can only start a statement or initialise."""

    operand: Operand


@dataclass
class QuantumCall(Node):
    """A call of a measurement-like ``defcal``: ``c = measure_iq(1.0) $0;``."""

    name: Identifier
    arguments: list[Expression]
    operands: list[Operand]


Operand = Identifier | IndexedIdentifier | HardwareQubit
Assignable = Identifier | IndexedIdentifier


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class Type(Node):
    """Base class of types."""


@dataclass
class ScalarType(Type):
    """Base class of the classical scalar types."""


@dataclass
class IntType(ScalarType):
    size: Expression | None = None


@dataclass
class UintType(ScalarType):
    size: Expression | None = None


@dataclass
class FloatType(ScalarType):
    size: Expression | None = None


@dataclass
class AngleType(ScalarType):
    size: Expression | None = None


@dataclass
class BitType(ScalarType):
    """``bit`` or the register ``bit[n]``."""

    size: Expression | None = None


@dataclass
class BoolType(ScalarType):
    pass


@dataclass
class ComplexType(ScalarType):
    base: FloatType | None = None


@dataclass
class DurationType(ScalarType):
    pass


@dataclass
class StretchType(ScalarType):
    pass


@dataclass
class ArrayType(Type):
    """``array[int[8], 2, 3]``."""

    base: ScalarType
    dimensions: list[Expression]


@dataclass
class ArrayReferenceType(Type):
    """An array argument of a subroutine.

    For example ``readonly array[int, 2]`` or ``mutable array[int, #dim = 2]``.

    Exactly one of ``dimensions`` (the sizes) and ``rank`` (``#dim = n``) is set.
    """

    access: AccessControl
    base: ScalarType
    dimensions: list[Expression] | None = None
    rank: Expression | None = None


@dataclass
class QubitType(Type):
    """The type of a qubit or a qubit register, used by subroutine arguments."""

    size: Expression | None = None


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


@dataclass
class Annotation(Node):
    """``@keyword command``, attached to the statement that follows it."""

    keyword: str
    command: str | None = None


@dataclass
class Statement(Node):
    """Base class of statements."""

    annotations: list[Annotation] = field(default_factory=list, kw_only=True)


@dataclass
class Pragma(Statement):
    """``pragma command``; only allowed at global scope."""

    command: str


@dataclass
class Program(Node):
    """A complete program. ``version`` is the text after ``OPENQASM``, if any."""

    statements: list[Statement]
    version: str | None = None
    comments: list[Comment] = field(default_factory=list, compare=False, repr=False)

    @property
    def version_tuple(self) -> tuple[int, ...] | None:
        if self.version is None:
            return None
        return tuple(int(part) for part in self.version.split("."))


@dataclass
class Include(Statement):
    filename: str


@dataclass
class CalibrationGrammar(Statement):
    """``defcalgrammar "openpulse";``."""

    name: str


@dataclass
class Block(Statement):
    """A braced scope ``{ ... }`` used as a statement."""

    statements: list[Statement]


@dataclass
class ExpressionStatement(Statement):
    expression: Expression


@dataclass
class QubitDeclaration(Statement):
    """``qubit[size] name;`` (or ``qreg name[size];`` when ``legacy_syntax``)."""

    name: Identifier
    size: Expression | None = None
    legacy_syntax: bool = field(default=False, compare=False)


@dataclass
class ClassicalDeclaration(Statement):
    """``type name [= init];`` (or ``creg name[size];`` when ``legacy_syntax``)."""

    type: ScalarType | ArrayType
    name: Identifier
    init: Expression | ArrayLiteral | MeasureExpression | QuantumCall | None = None
    legacy_syntax: bool = field(default=False, compare=False)


@dataclass
class ConstDeclaration(Statement):
    type: ScalarType
    name: Identifier
    init: Expression | ArrayLiteral | MeasureExpression | QuantumCall


@dataclass
class IODeclaration(Statement):
    io: IOKeyword
    type: ScalarType | ArrayType
    name: Identifier


@dataclass
class AliasDeclaration(Statement):
    """``let name = value;``."""

    name: Identifier
    value: Expression


@dataclass
class Assignment(Statement):
    """``target op value;``. A plain ``target = measure q;`` is a :class:`MeasureStatement`."""

    target: Assignable
    op: AssignmentOperator
    value: Expression | MeasureExpression | QuantumCall


@dataclass
class GateModifier(Node):
    """``inv @``, ``pow(k) @``, ``ctrl(n) @`` or ``negctrl(n) @``."""

    kind: GateModifierKind
    argument: Expression | None = None


@dataclass
class GateDefinition(Statement):
    name: Identifier
    parameters: list[Identifier]
    qubits: list[Identifier]
    body: list[Statement]


@dataclass
class OpaqueDefinition(Statement):
    """OpenQASM 2 ``opaque name(params) qubits;``; only accepted in version 2 programs."""

    name: Identifier
    parameters: list[Identifier]
    qubits: list[Identifier]


@dataclass
class GateCall(Statement):
    modifiers: list[GateModifier]
    name: Identifier
    arguments: list[Expression]
    qubits: list[Operand]
    duration: Expression | None = None


@dataclass
class GlobalPhase(Statement):
    """``gphase(angle);``, optionally modified (``ctrl @ gphase(a) q;``)."""

    modifiers: list[GateModifier]
    argument: Expression
    qubits: list[Operand] = field(default_factory=list)
    duration: Expression | None = None


@dataclass
class MeasureStatement(Statement):
    """``measure q;``, ``measure q -> c;`` or ``c = measure q;``."""

    measure: MeasureExpression | QuantumCall
    target: Assignable | None = None
    arrow_syntax: bool = field(default=False, compare=False)


@dataclass
class Reset(Statement):
    operand: Operand


@dataclass
class Barrier(Statement):
    operands: list[Operand]


@dataclass
class Delay(Statement):
    duration: Expression
    operands: list[Operand]


@dataclass
class Nop(Statement):
    operands: list[Operand]


@dataclass
class Box(Statement):
    duration: Expression | None
    body: list[Statement]


@dataclass
class ClassicalArgument(Node):
    type: ScalarType | ArrayReferenceType
    name: Identifier
    legacy_syntax: bool = field(default=False, compare=False)


@dataclass
class QuantumArgument(Node):
    name: Identifier
    size: Expression | None = None
    legacy_syntax: bool = field(default=False, compare=False)


@dataclass
class SubroutineDefinition(Statement):
    name: Identifier
    arguments: list[ClassicalArgument | QuantumArgument]
    body: list[Statement]
    return_type: ScalarType | None = None


@dataclass
class ExternArgument(Node):
    type: ScalarType | ArrayReferenceType
    legacy_syntax: bool = field(default=False, compare=False)


@dataclass
class ExternDeclaration(Statement):
    name: Identifier
    arguments: list[ExternArgument]
    return_type: ScalarType | None = None


@dataclass
class Return(Statement):
    value: Expression | MeasureExpression | QuantumCall | None = None


@dataclass
class Break(Statement):
    pass


@dataclass
class Continue(Statement):
    pass


@dataclass
class End(Statement):
    pass


@dataclass
class If(Statement):
    condition: Expression
    then_body: list[Statement]
    else_body: list[Statement] = field(default_factory=list)


@dataclass
class While(Statement):
    condition: Expression
    body: list[Statement]


@dataclass
class For(Statement):
    """``for type variable in iterable { ... }``.

    ``iterable`` is a :class:`Range` (``[0:4]``), a :class:`DiscreteSet`
    (``{1, 2}``) or an expression evaluating to an array or bit register.
    """

    type: ScalarType
    variable: Identifier
    iterable: Range | DiscreteSet | Expression
    body: list[Statement]


@dataclass
class SwitchCase(Node):
    values: list[Expression]
    body: list[Statement]


@dataclass
class Switch(Statement):
    target: Expression
    cases: list[SwitchCase]
    default: list[Statement] | None = None


@dataclass
class CalibrationBlock(Statement):
    """``cal { ... }``; the body is kept verbatim."""

    body: str


@dataclass
class CalibrationDefinition(Statement):
    """``defcal name(args) operands -> type { ... }``; the body is kept verbatim."""

    name: Identifier
    arguments: list[ClassicalArgument | Expression]
    operands: list[Identifier | HardwareQubit]
    body: str
    return_type: ScalarType | None = None
