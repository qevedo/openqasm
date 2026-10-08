"""Recursive-descent parser producing :mod:`openqasm.ast` trees.

The accepted language is the OpenQASM 3 grammar of the specification. On top
of the grammar, the parser enforces the rules that depend only on where a
statement appears (``break`` outside a loop, a ``qubit`` declaration inside
a block, ``measure`` inside a gate, ...). Rules that need to know what names
refer to are left to :mod:`openqasm.semantic`.

Programs declaring ``OPENQASM 2.0`` are parsed with the OpenQASM 3 grammar
plus the OpenQASM 2 ``opaque`` statement, which covers the OpenQASM 2
language.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

from . import ast
from .errors import QasmSyntaxError, UnsupportedVersionError
from .lexer import TYPE_KEYWORDS, Token, is_bitstring, tokenize

__all__ = ["parse", "parse_expression", "SUPPORTED_VERSIONS"]

#: Language versions accepted in the ``OPENQASM`` statement.
SUPPORTED_VERSIONS = ("2.0", "3.0", "3.1")

_NodeT = TypeVar("_NodeT", bound=ast.Node)

# Binding powers of the binary operators, loosest first. The table matches the
# precedence of the specification grammar.
_BINARY_POWER = {
    "||": 1,
    "&&": 2,
    "|": 3,
    "^": 4,
    "&": 5,
    "==": 6,
    "!=": 6,
    "<": 7,
    ">": 7,
    "<=": 7,
    ">=": 7,
    "<<": 8,
    ">>": 8,
    "+": 9,
    "-": 9,
    "*": 10,
    "/": 10,
    "%": 10,
    "**": 12,
}
_UNARY_POWER = 11
_POSTFIX_POWER = 13

_ASSIGNMENT_OPERATORS = frozenset(op.value for op in ast.AssignmentOperator)
_SCALAR_TYPE_KEYWORDS = TYPE_KEYWORDS - {"array"}
_SIZED_TYPES = {
    "int": ast.IntType,
    "uint": ast.UintType,
    "float": ast.FloatType,
    "angle": ast.AngleType,
    "bit": ast.BitType,
}
_ARRAY_BASE_TYPES = (
    ast.BitType,
    ast.IntType,
    ast.UintType,
    ast.FloatType,
    ast.AngleType,
    ast.DurationType,
    ast.BoolType,
    ast.ComplexType,
)

# Kinds of the enclosing constructs, used for placement rules.
_GLOBAL, _GATE, _DEF, _LOOP, _BLOCK = "global", "gate", "def", "loop", "block"


def _describe(token: Token) -> str:
    if token.kind == "EOF":
        return "end of file"
    if token.kind == "IDENTIFIER":
        return f"identifier '{token.text}'"
    if token.kind in ("INTEGER", "FLOAT", "IMAGINARY", "TIMING"):
        return f"number '{token.text}'"
    if token.kind == "STRING":
        return f"string {token.text}"
    if token.kind == "CALIBRATION":
        return "calibration block"
    return f"'{token.text}'"


class _Parser:
    def __init__(self, tokens: list[Token], filename: str | None, ignore_version: bool):
        self.tokens = tokens
        self.pos = 0
        self.filename = filename
        self.ignore_version = ignore_version
        self.version: tuple[int, ...] | None = None
        # A stack of contexts (program, gate, subroutine), each a stack of the
        # scopes opened inside it.
        self.contexts: list[list[str]] = []

    # -- token helpers --------------------------------------------------------

    def peek(self, offset: int = 0) -> Token:
        index = min(self.pos + offset, len(self.tokens) - 1)
        return self.tokens[index]

    def at(self, *kinds: str) -> bool:
        return self.tokens[self.pos].kind in kinds

    @property
    def previous(self) -> Token:
        return self.tokens[self.pos - 1]

    def advance(self) -> Token:
        token = self.tokens[self.pos]
        if token.kind != "EOF":
            self.pos += 1
        return token

    def accept(self, kind: str) -> Token | None:
        if self.at(kind):
            return self.advance()
        return None

    def expect(self, kind: str, what: str | None = None) -> Token:
        if self.at(kind):
            return self.advance()
        raise self.unexpected(what or f"'{kind}'")

    def unexpected(self, expected: str) -> QasmSyntaxError:
        token = self.peek()
        return QasmSyntaxError(f"expected {expected}, found {_describe(token)}", token.span)

    def error(self, message: str, span: ast.Span | None) -> QasmSyntaxError:
        return QasmSyntaxError(message, span)

    def finish(self, node: _NodeT, start: Token) -> _NodeT:
        """Give ``node`` the span from ``start`` to the last consumed token."""
        end = self.previous.span if self.pos > 0 else start.span
        node.span = ast.Span(
            start.span.start_line,
            start.span.start_column,
            end.end_line,
            end.end_column,
            self.filename,
        )
        return node

    def speculate(self, attempt: Callable[[], _NodeT]) -> _NodeT | None:
        """Run ``attempt``; on a syntax error, rewind and return ``None``."""
        saved = self.pos
        try:
            return attempt()
        except QasmSyntaxError:
            self.pos = saved
            return None

    # -- contexts -------------------------------------------------------------

    @contextmanager
    def context(self, kind: str) -> Iterator[None]:
        self.contexts.append([kind])
        try:
            yield
        finally:
            self.contexts.pop()

    @contextmanager
    def scope(self, kind: str) -> Iterator[None]:
        self.contexts[-1].append(kind)
        try:
            yield
        finally:
            self.contexts[-1].pop()

    def in_global_scope(self) -> bool:
        return len(self.contexts) == 1 and len(self.contexts[0]) == 1

    def in_gate(self) -> bool:
        return self.contexts[-1][0] == _GATE

    def in_subroutine(self) -> bool:
        return self.contexts[-1][0] == _DEF

    def in_loop(self) -> bool:
        return _LOOP in self.contexts[-1]

    def require_global(self, start: Token, message: str) -> None:
        if not self.in_global_scope():
            raise self.error(message, start.span)

    @property
    def openqasm2(self) -> bool:
        return self.version is not None and self.version[0] == 2

    # -- program --------------------------------------------------------------

    def program(self) -> ast.Program:
        start = self.peek()
        version = None
        if self.at("OPENQASM"):
            keyword = self.advance()
            version_token = self.expect("VERSION", "a version number")
            self.expect(";")
            version = version_token.value
            self.check_version(version, keyword)
        statements: list[ast.Statement] = []
        with self.context(_GLOBAL):
            while not self.at("EOF"):
                statements.append(self.statement_or_scope())
        return self.finish(ast.Program(statements, version), start)

    def check_version(self, version: str, keyword: Token) -> None:
        self.version = tuple(int(part) for part in version.split("."))
        if self.ignore_version:
            return
        normalised = version if "." in version else f"{version}.0"
        if normalised not in SUPPORTED_VERSIONS:
            raise UnsupportedVersionError(
                f"unsupported version '{version}'; supported versions are "
                + ", ".join(SUPPORTED_VERSIONS),
                keyword.span.merge(self.previous.span),
            )

    # -- statements -----------------------------------------------------------

    def statement_or_scope(self) -> ast.Statement:
        if self.at("{"):
            return self.block()
        return self.statement()

    def block(self) -> ast.Block:
        start = self.peek()
        with self.scope(_BLOCK):
            statements = self.braced_statements()
        return self.finish(ast.Block(statements), start)

    def braced_statements(self) -> list[ast.Statement]:
        self.expect("{")
        statements = []
        while not self.at("}"):
            if self.at("EOF"):
                raise self.unexpected("'}'")
            statements.append(self.statement_or_scope())
        self.advance()
        return statements

    def body(self, kind: str) -> list[ast.Statement]:
        """The body of a control-flow statement: a scope or a single statement."""
        with self.scope(kind):
            if self.at("{"):
                return self.braced_statements()
            return [self.statement()]

    def statement(self) -> ast.Statement:
        start = self.peek()
        if self.at("pragma"):
            return self.pragma()
        annotations = []
        while self.at("ANNOTATION"):
            token = self.advance()
            content = self.accept("LINE_CONTENT")
            annotation = ast.Annotation(token.value, content.value if content else None)
            annotations.append(self.finish(annotation, token))
        if annotations and self.at("pragma"):
            raise self.error("pragmas cannot be annotated", self.peek().span)
        statement = self.unannotated_statement()
        statement.annotations = annotations
        return self.finish(statement, start)

    def unannotated_statement(self) -> ast.Statement:
        kind = self.peek().kind
        method = _STATEMENT_KEYWORDS.get(kind)
        if method is not None:
            return method(self)
        if kind in TYPE_KEYWORDS:
            return self.declaration_or_expression()
        if kind in ("inv", "pow", "ctrl", "negctrl", "gphase"):
            return self.gate_call()
        if kind == "IDENTIFIER":
            if (
                self.openqasm2
                and self.peek().text == "opaque"
                and self.peek(1).kind == "IDENTIFIER"
            ):
                return self.opaque()
            return self.identifier_statement()
        if kind == "OPENQASM":
            raise self.error(
                "the 'OPENQASM' version statement must be the first statement", self.peek().span
            )
        if kind == "EOF":
            raise self.unexpected("a statement")
        return self.expression_statement()

    def pragma(self) -> ast.Pragma:
        start = self.advance()
        self.require_global(start, "pragmas must be global")
        content = self.expect("LINE_CONTENT", "pragma content")
        return self.finish(ast.Pragma(content.value), start)

    def include(self) -> ast.Include:
        start = self.advance()
        self.require_global(start, "'include' statements must be global")
        filename = self.expect("STRING", "a file name string")
        if not filename.value:
            raise self.error("empty file name", filename.span)
        self.expect(";")
        return ast.Include(filename.value)

    def defcalgrammar(self) -> ast.CalibrationGrammar:
        start = self.advance()
        self.require_global(start, "'defcalgrammar' statements must be global")
        name = self.expect("STRING", "a calibration grammar name string")
        if not name.value:
            raise self.error("empty calibration grammar name", name.span)
        self.expect(";")
        return ast.CalibrationGrammar(name.value)

    def simple(self, node_type: type, check: Callable[[], bool], message: str) -> ast.Statement:
        start = self.advance()
        if not check():
            raise self.error(message, start.span)
        self.expect(";")
        return node_type()

    def break_(self) -> ast.Statement:
        return self.simple(ast.Break, self.in_loop, "'break' statement outside loop")

    def continue_(self) -> ast.Statement:
        return self.simple(ast.Continue, self.in_loop, "'continue' statement outside loop")

    def end(self) -> ast.Statement:
        return self.simple(ast.End, lambda: True, "")

    def return_(self) -> ast.Return:
        start = self.advance()
        if not self.in_subroutine():
            raise self.error("'return' statement outside subroutine", start.span)
        value = None
        if not self.at(";"):
            value = self.rvalue()
        self.expect(";")
        return ast.Return(value)

    def if_(self) -> ast.If:
        self.advance()
        self.expect("(")
        condition = self.expression()
        self.expect(")")
        then_body = self.body(_BLOCK)
        else_body = []
        if self.accept("else"):
            else_body = self.body(_BLOCK)
        return ast.If(condition, then_body, else_body)

    def while_(self) -> ast.While:
        self.advance()
        self.expect("(")
        condition = self.expression()
        self.expect(")")
        return ast.While(condition, self.body(_LOOP))

    def for_(self) -> ast.For:
        self.advance()
        if not self.at(*_SCALAR_TYPE_KEYWORDS):
            raise self.unexpected("the type of the loop variable")
        type_ = self.scalar_type()
        variable = self.identifier()
        self.expect("in")
        iterable: ast.Range | ast.DiscreteSet | ast.Expression
        if self.at("{"):
            iterable = self.discrete_set()
        elif self.at("["):
            self.advance()
            iterable = self.range_or_expression(require_range=True)
            self.expect("]")
        else:
            iterable = self.expression()
        return ast.For(type_, variable, iterable, self.body(_LOOP))

    def switch(self) -> ast.Switch:
        self.advance()
        self.expect("(")
        target = self.expression()
        self.expect(")")
        self.expect("{")
        cases: list[ast.SwitchCase] = []
        default: list[ast.Statement] | None = None
        while not self.accept("}"):
            token = self.peek()
            if self.accept("case"):
                if default is not None:
                    raise self.error("'case' statement after 'default'", token.span)
                values = self.expression_list(closing="{")
                if not values:
                    raise self.unexpected("a case value")
                with self.scope(_BLOCK):
                    body = self.braced_statements()
                cases.append(self.finish(ast.SwitchCase(values, body), token))
            elif self.accept("default"):
                if default is not None:
                    raise self.error("multiple 'default' cases", token.span)
                with self.scope(_BLOCK):
                    default = self.braced_statements()
            else:
                raise self.unexpected("'case', 'default' or '}'")
        return ast.Switch(target, cases, default)

    def box(self) -> ast.Box:
        self.advance()
        duration = self.designator() if self.at("[") else None
        with self.scope(_BLOCK):
            body = self.braced_statements()
        return ast.Box(duration, body)

    def barrier(self) -> ast.Barrier:
        self.advance()
        operands = self.operand_list() if not self.at(";") else []
        self.expect(";")
        return ast.Barrier(operands)

    def nop(self) -> ast.Nop:
        self.advance()
        operands = self.operand_list() if not self.at(";") else []
        self.expect(";")
        return ast.Nop(operands)

    def delay(self) -> ast.Delay:
        self.advance()
        if not self.at("["):
            raise self.unexpected("a duration designator '['")
        duration = self.designator()
        operands = self.operand_list() if not self.at(";") else []
        self.expect(";")
        return ast.Delay(duration, operands)

    def reset(self) -> ast.Reset:
        start = self.advance()
        if self.in_gate():
            raise self.error("cannot have a non-unitary 'reset' instruction in a gate", start.span)
        operand = self.operand()
        self.expect(";")
        return ast.Reset(operand)

    def measure_statement(self) -> ast.MeasureStatement:
        measure = self.measure_expression()
        target = None
        arrow = bool(self.accept("->"))
        if arrow:
            target = self.indexed_identifier()
        self.expect(";")
        return ast.MeasureStatement(measure, target, arrow_syntax=arrow)

    def measure_expression(self) -> ast.MeasureExpression:
        start = self.expect("measure")
        if self.in_gate():
            raise self.error(
                "cannot have a non-unitary 'measure' instruction in a gate", start.span
            )
        return self.finish(ast.MeasureExpression(self.operand()), start)

    def calibration(self) -> ast.CalibrationBlock:
        self.advance()
        self.expect("{")
        body = self.accept("CALIBRATION")
        self.expect("}")
        return ast.CalibrationBlock(body.value if body else "")

    def defcal(self) -> ast.CalibrationDefinition:
        self.advance()
        target = self.peek()
        if self.at("measure", "reset", "delay", "IDENTIFIER"):
            self.advance()
        else:
            raise self.unexpected("a calibration target")
        name = self.finish(ast.Identifier(target.text), target)
        arguments: list[ast.ClassicalArgument | ast.Expression] = []
        if self.accept("("):
            arguments = self.comma_list(self.defcal_argument, closing=")")
            self.expect(")")
        operands: list[ast.Identifier | ast.HardwareQubit] = []
        while True:
            token = self.peek()
            if self.accept("HARDWARE_QUBIT"):
                operands.append(self.finish(ast.HardwareQubit(token.value), token))
            elif self.at("IDENTIFIER"):
                operands.append(self.identifier())
            else:
                raise self.unexpected("a calibration operand")
            if not self.accept(",") or not self.at("HARDWARE_QUBIT", "IDENTIFIER"):
                break
        return_type = self.return_signature()
        self.expect("{")
        body = self.accept("CALIBRATION")
        self.expect("}")
        return ast.CalibrationDefinition(
            name, arguments, operands, body.value if body else "", return_type
        )

    def defcal_argument(self) -> ast.ClassicalArgument | ast.Expression:
        argument = self.speculate(self.argument_definition)
        if argument is not None:
            if isinstance(argument, ast.QuantumArgument):
                raise self.error("calibration arguments must be classical", argument.span)
            return argument
        return self.expression()

    def gate_definition(self) -> ast.GateDefinition:
        start = self.advance()
        self.require_global(start, "gate definitions must be global")
        name = self.identifier()
        parameters: list[ast.Identifier] = []
        if self.accept("("):
            parameters = self.comma_list(self.identifier, closing=")")
            self.expect(")")
        qubits = self.comma_list(self.identifier, closing="{")
        if not qubits:
            raise self.unexpected("a qubit name")
        with self.context(_GATE):
            body = self.braced_statements()
        return ast.GateDefinition(name, parameters, qubits, body)

    def opaque(self) -> ast.OpaqueDefinition:
        start = self.advance()
        self.require_global(start, "opaque definitions must be global")
        name = self.identifier()
        parameters: list[ast.Identifier] = []
        if self.accept("("):
            parameters = self.comma_list(self.identifier, closing=")")
            self.expect(")")
        qubits = self.comma_list(self.identifier, closing=";")
        if not qubits:
            raise self.unexpected("a qubit name")
        self.expect(";")
        return ast.OpaqueDefinition(name, parameters, qubits)

    def subroutine_definition(self) -> ast.SubroutineDefinition:
        start = self.advance()
        self.require_global(start, "subroutine definitions must be global")
        name = self.identifier()
        self.expect("(")
        arguments = self.comma_list(self.argument_definition, closing=")")
        self.expect(")")
        return_type = self.return_signature()
        with self.context(_DEF):
            body = self.braced_statements()
        return ast.SubroutineDefinition(name, arguments, body, return_type)

    def extern(self) -> ast.ExternDeclaration:
        start = self.advance()
        self.require_global(start, "extern declarations must be global")
        name = self.identifier()
        self.expect("(")
        arguments = self.comma_list(self.extern_argument, closing=")")
        self.expect(")")
        return_type = self.return_signature()
        self.expect(";")
        return ast.ExternDeclaration(name, arguments, return_type)

    def return_signature(self) -> ast.ScalarType | None:
        if not self.accept("->"):
            return None
        if not self.at(*_SCALAR_TYPE_KEYWORDS):
            raise self.unexpected("a return type")
        return self.scalar_type()

    def argument_definition(self) -> ast.ClassicalArgument | ast.QuantumArgument:
        start = self.peek()
        if self.accept("qubit"):
            size = self.designator() if self.at("[") else None
            return self.finish(ast.QuantumArgument(self.identifier(), size), start)
        if self.at("qreg", "creg"):
            keyword = self.advance()
            name = self.identifier()
            size = self.designator() if self.at("[") else None
            if keyword.kind == "qreg":
                argument = ast.QuantumArgument(name, size, legacy_syntax=True)
                return self.finish(argument, start)
            bit_type = self.finish(ast.BitType(size), keyword)
            return self.finish(ast.ClassicalArgument(bit_type, name, legacy_syntax=True), start)
        if self.at("readonly", "mutable"):
            type_: ast.ScalarType | ast.ArrayReferenceType = self.array_reference_type()
        elif self.at(*_SCALAR_TYPE_KEYWORDS):
            type_ = self.scalar_type()
        else:
            raise self.unexpected("an argument type")
        return self.finish(ast.ClassicalArgument(type_, self.identifier()), start)

    def extern_argument(self) -> ast.ExternArgument:
        start = self.peek()
        if self.at("creg"):
            keyword = self.advance()
            size = self.designator() if self.at("[") else None
            bit_type = self.finish(ast.BitType(size), keyword)
            return self.finish(ast.ExternArgument(bit_type, legacy_syntax=True), start)
        if self.at("readonly", "mutable"):
            return self.finish(ast.ExternArgument(self.array_reference_type()), start)
        if self.at(*_SCALAR_TYPE_KEYWORDS):
            return self.finish(ast.ExternArgument(self.scalar_type()), start)
        raise self.unexpected("an argument type")

    # -- declarations ---------------------------------------------------------

    def const_declaration(self) -> ast.ConstDeclaration:
        self.advance()
        if not self.at(*_SCALAR_TYPE_KEYWORDS):
            raise self.unexpected("the type of the constant")
        type_ = self.scalar_type()
        name = self.identifier()
        self.expect("=")
        init = self.declaration_initialiser()
        self.expect(";")
        return ast.ConstDeclaration(type_, name, init)

    def io_declaration(self) -> ast.IODeclaration:
        keyword = self.advance()
        self.require_global(keyword, f"'{keyword.kind}' declarations must be global")
        if not self.at(*TYPE_KEYWORDS):
            raise self.unexpected("a type")
        type_ = self.array_type() if self.at("array") else self.scalar_type()
        name = self.identifier()
        self.expect(";")
        return ast.IODeclaration(ast.IOKeyword(keyword.kind), type_, name)

    def let(self) -> ast.AliasDeclaration:
        self.advance()
        name = self.identifier()
        self.expect("=")
        start = self.peek()
        value = self.expression()
        while self.accept("++"):
            value = self.finish(ast.Concatenation(value, self.expression()), start)
        self.expect(";")
        return ast.AliasDeclaration(name, value)

    def qubit_declaration(self) -> ast.QubitDeclaration:
        start = self.advance()
        self.require_global(start, "qubit declarations must be global")
        size = self.designator() if self.at("[") else None
        name = self.identifier()
        self.expect(";")
        return ast.QubitDeclaration(name, size)

    def old_style_declaration(self) -> ast.Statement:
        """OpenQASM 2 style ``qreg q[n];`` and ``creg c[n];``."""
        keyword = self.advance()
        name = self.identifier()
        size = None
        type_span = keyword.span
        if self.at("["):
            designator_start = self.peek()
            size = self.designator()
            type_span = keyword.span.merge(self.previous.span)
            if _is_negative_or_zero(size):
                raise self.error(f"{keyword.kind} size must be positive", designator_start.span)
        self.expect(";")
        if keyword.kind == "qreg":
            self.require_global(keyword, "qubit declarations must be global")
            return ast.QubitDeclaration(name, size, legacy_syntax=True)
        if self.in_gate():
            raise self.error("cannot declare classical variables in a gate", keyword.span)
        bit_type = ast.BitType(size, span=type_span)
        return ast.ClassicalDeclaration(bit_type, name, None, legacy_syntax=True)

    def declaration_or_expression(self) -> ast.Statement:
        """A statement starting with a type: a declaration, or a cast expression."""
        if self.cast_follows():
            return self.expression_statement()
        return self.classical_declaration()

    def cast_follows(self) -> bool:
        saved = self.pos
        try:
            if self.at("array"):
                self.array_type()
            else:
                self.scalar_type()
            return self.at("(")
        except QasmSyntaxError:
            return False
        finally:
            self.pos = saved

    def classical_declaration(self) -> ast.ClassicalDeclaration:
        start = self.peek()
        is_array = self.at("array")
        type_ = self.array_type() if is_array else self.scalar_type()
        if not self.at("IDENTIFIER"):
            raise self.unexpected("an identifier")
        if self.in_gate():
            raise self.error("cannot declare classical variables in a gate", start.span)
        if is_array and not self.in_global_scope():
            raise self.error("arrays can only be declared globally", start.span)
        name = self.identifier()
        init = None
        if self.accept("="):
            init = self.declaration_initialiser()
        self.expect(";")
        return ast.ClassicalDeclaration(type_, name, init)

    def declaration_initialiser(
        self,
    ) -> ast.Expression | ast.ArrayLiteral | ast.MeasureExpression | ast.QuantumCall:
        if self.at("{"):
            return self.array_literal()
        return self.rvalue()

    def array_literal(self) -> ast.ArrayLiteral:
        start = self.expect("{")
        values: list[ast.Expression] = []
        while not self.at("}"):
            values.append(self.array_literal() if self.at("{") else self.expression())
            if not self.accept(","):
                break
        self.expect("}")
        return self.finish(ast.ArrayLiteral(values), start)

    # -- quantum instructions ---------------------------------------------------

    def gate_modifiers(self) -> list[ast.GateModifier]:
        modifiers = []
        while self.at("inv", "pow", "ctrl", "negctrl"):
            start = self.advance()
            argument = None
            if start.kind == "pow":
                self.expect("(")
                argument = self.expression()
                self.expect(")")
            elif start.kind in ("ctrl", "negctrl") and self.accept("("):
                argument = self.expression()
                self.expect(")")
            self.expect("@")
            modifier = ast.GateModifier(ast.GateModifierKind(start.kind), argument)
            modifiers.append(self.finish(modifier, start))
        return modifiers

    def gate_call(self) -> ast.Statement:
        """A gate call that starts with modifiers or ``gphase``."""
        modifiers = self.gate_modifiers()
        if self.at("gphase"):
            start = self.advance()
            arguments = self.call_arguments() if self.at("(") else []
            duration = self.designator() if self.at("[") else None
            qubits = self.operand_list() if not self.at(";") else []
            self.expect(";")
            if len(arguments) != 1:
                raise self.error(
                    f"'gphase' takes exactly one argument, but received {len(arguments)}",
                    start.span,
                )
            return ast.GlobalPhase(modifiers, arguments[0], qubits, duration)
        if not self.at("IDENTIFIER"):
            raise self.unexpected("a gate name")
        name = self.identifier()
        arguments = self.call_arguments() if self.at("(") else []
        duration = self.designator() if self.at("[") else None
        qubits = self.operand_list()
        self.expect(";")
        return ast.GateCall(modifiers, name, arguments, qubits, duration)

    def skip_group(self, index: int, opening: str, closing: str) -> int:
        """Index just past the balanced ``opening ... closing`` group at ``index``."""
        if self.tokens[index].kind != opening:
            return index
        depth = 0
        while index < len(self.tokens):
            kind = self.tokens[index].kind
            if kind == opening:
                depth += 1
            elif kind == closing:
                depth -= 1
                if depth == 0:
                    return index + 1
            elif kind == "EOF":
                return index
            index += 1
        return index

    def operands_follow(self, with_duration: bool) -> bool:
        """Whether the identifier at the cursor starts a gate call or a quantum call:
        ``name (args)? [duration]? operand``."""
        index = self.skip_group(self.pos + 1, "(", ")")
        if with_duration:
            index = self.skip_group(index, "[", "]")
        return self.tokens[index].kind in ("IDENTIFIER", "HARDWARE_QUBIT")

    def identifier_statement(self) -> ast.Statement:
        """A statement starting with an identifier.

        ``name(args)[duration] operands;`` is a gate call, ``name(args) operands
        -> target;`` a measurement through a ``defcal``; anything else is an
        assignment or an expression statement.
        """
        if not self.operands_follow(with_duration=True):
            return self.expression_statement()
        start = self.peek()
        name = self.identifier()
        arguments = self.call_arguments() if self.at("(") else []
        duration = self.designator() if self.at("[") else None
        qubits = self.operand_list()
        if self.at("->"):
            if duration is not None:
                raise self.error("a measurement cannot have a duration", duration.span)
            if self.in_gate():
                raise self.error(
                    "cannot have a non-unitary measure-like quantum call expression"
                    " instruction in a gate",
                    name.span,
                )
            call = self.finish(ast.QuantumCall(name, arguments, qubits), start)
            self.advance()
            target = self.indexed_identifier()
            self.expect(";")
            return ast.MeasureStatement(call, target, arrow_syntax=True)
        self.expect(";")
        return ast.GateCall([], name, arguments, qubits, duration)

    def expression_statement(self) -> ast.Statement:
        start = self.peek()
        expression = self.expression()
        if self.at(*_ASSIGNMENT_OPERATORS):
            return self.assignment(expression, start)
        self.expect(";")
        return ast.ExpressionStatement(expression)

    def assignment(self, target_expression: ast.Expression, start: Token) -> ast.Statement:
        target = self.assignable(target_expression)
        operator = self.advance()
        if self.in_gate():
            raise self.error("cannot assign to classical parameters in a gate", start.span)
        value = self.rvalue()
        self.expect(";")
        op = ast.AssignmentOperator(operator.kind)
        if op is ast.AssignmentOperator.ASSIGN and isinstance(
            value, (ast.MeasureExpression, ast.QuantumCall)
        ):
            return ast.MeasureStatement(value, target)
        return ast.Assignment(target, op, value)

    def assignable(self, expression: ast.Expression) -> ast.Assignable:
        """Turn ``a`` or ``a[i][j]`` into an assignment target."""
        indices: list[ast.Index] = []
        node = expression
        while isinstance(node, ast.IndexExpression):
            indices.append(node.index)
            node = node.collection
        if not isinstance(node, ast.Identifier):
            raise self.error("invalid assignment target", expression.span)
        if not indices:
            return node
        indexed = ast.IndexedIdentifier(node, indices[::-1])
        indexed.span = expression.span
        return indexed

    def rvalue(self) -> ast.Expression | ast.MeasureExpression | ast.QuantumCall:
        """An expression, a ``measure`` or a measurement ``defcal`` call."""
        if self.at("measure"):
            return self.measure_expression()
        if self.at("IDENTIFIER") and self.operands_follow(with_duration=False):
            return self.quantum_call()
        return self.expression()

    def quantum_call(self) -> ast.QuantumCall:
        start = self.peek()
        name = self.identifier()
        arguments = self.call_arguments() if self.at("(") else []
        if self.in_gate():
            raise self.error(
                "cannot have a non-unitary measure-like quantum call expression instruction"
                " in a gate",
                start.span,
            )
        return self.finish(ast.QuantumCall(name, arguments, self.operand_list()), start)

    def operand_list(self) -> list[ast.Operand]:
        operands = [self.operand()]
        while self.accept(","):
            if not self.at("IDENTIFIER", "HARDWARE_QUBIT"):
                break
            operands.append(self.operand())
        return operands

    def operand(self) -> ast.Operand:
        token = self.peek()
        if self.accept("HARDWARE_QUBIT"):
            return self.finish(ast.HardwareQubit(token.value), token)
        if self.at("IDENTIFIER"):
            return self.indexed_identifier()
        raise self.unexpected("a qubit operand")

    def indexed_identifier(self) -> ast.Assignable:
        start = self.peek()
        name = self.identifier()
        if not self.at("["):
            return name
        indices = []
        while self.at("["):
            indices.append(self.index_operator())
        return self.finish(ast.IndexedIdentifier(name, indices), start)

    # -- types ------------------------------------------------------------------

    def designator(self) -> ast.Expression:
        self.expect("[")
        expression = self.expression()
        self.expect("]")
        return expression

    def scalar_type(self) -> ast.ScalarType:
        start = self.advance()
        kind = start.kind
        if kind in _SIZED_TYPES:
            size = None
            if self.at("["):
                designator_start = self.peek()
                size = self.designator()
                if kind in ("int", "uint", "angle") and _is_negative_or_zero(size):
                    raise self.error(f"{kind} size must be positive", designator_start.span)
            return self.finish(_SIZED_TYPES[kind](size), start)
        if kind == "bool":
            return self.finish(ast.BoolType(), start)
        if kind == "duration":
            return self.finish(ast.DurationType(), start)
        if kind == "stretch":
            return self.finish(ast.StretchType(), start)
        if kind == "complex":
            base = None
            if self.accept("["):
                base_start = self.peek()
                if not self.at(*_SCALAR_TYPE_KEYWORDS):
                    raise self.unexpected("a float type")
                base_type = self.scalar_type()
                if not isinstance(base_type, ast.FloatType):
                    raise self.error("invalid type of complex components", base_start.span)
                base = base_type
                self.expect("]")
            return self.finish(ast.ComplexType(base), start)
        self.pos -= 1
        raise self.unexpected("a type")

    def array_type(self) -> ast.ArrayType:
        start = self.expect("array")
        self.expect("[")
        base = self.array_base_type()
        self.expect(",")
        dimensions = self.expression_list(closing="]")
        if not dimensions:
            raise self.unexpected("an array dimension")
        for dimension in dimensions:
            if (
                isinstance(dimension, ast.UnaryExpression)
                and dimension.op is ast.UnaryOperator.NEGATE
            ):
                raise self.error("all array dimensions must be non-negative", dimension.span)
        self.expect("]")
        return self.finish(ast.ArrayType(base, dimensions), start)

    def array_base_type(self) -> ast.ScalarType:
        base_start = self.peek()
        if not self.at(*_SCALAR_TYPE_KEYWORDS):
            raise self.unexpected("an array element type")
        base = self.scalar_type()
        if not isinstance(base, _ARRAY_BASE_TYPES):
            raise self.error("invalid scalar type for array", base_start.span)
        return base

    def array_reference_type(self) -> ast.ArrayReferenceType:
        start = self.advance()
        access = ast.AccessControl(start.kind)
        self.expect("array")
        self.expect("[")
        base = self.array_base_type()
        self.expect(",")
        if self.accept("DIM"):
            self.expect("=")
            rank = self.expression()
            self.expect("]")
            return self.finish(ast.ArrayReferenceType(access, base, rank=rank), start)
        dimensions = self.expression_list(closing="]")
        if not dimensions:
            raise self.unexpected("an array dimension")
        self.expect("]")
        return self.finish(ast.ArrayReferenceType(access, base, dimensions=dimensions), start)

    # -- expressions ------------------------------------------------------------

    def identifier(self) -> ast.Identifier:
        token = self.expect("IDENTIFIER", "an identifier")
        return self.finish(ast.Identifier(token.value), token)

    def comma_list(self, item: Callable[[], _NodeT], closing: str) -> list[_NodeT]:
        """``item (, item)* ,?`` ending before ``closing``; may be empty."""
        items: list[_NodeT] = []
        while not self.at(closing):
            items.append(item())
            if not self.accept(","):
                break
        return items

    def expression_list(self, closing: str) -> list[ast.Expression]:
        return self.comma_list(self.expression, closing)

    def call_arguments(self) -> list[ast.Expression]:
        self.expect("(")
        arguments = self.expression_list(closing=")")
        self.expect(")")
        return arguments

    def discrete_set(self) -> ast.DiscreteSet:
        start = self.expect("{")
        values = self.expression_list(closing="}")
        if not values:
            raise self.unexpected("a set element")
        self.expect("}")
        return self.finish(ast.DiscreteSet(values), start)

    def range_or_expression(self, require_range: bool = False) -> ast.IndexElement:
        """``expr``, or a range ``start:end`` / ``start:step:end`` with optional parts."""
        start_token = self.peek()
        start = None if self.at(":") else self.expression()
        if not self.at(":"):
            if require_range:
                raise self.unexpected("':'")
            assert start is not None
            return start
        self.advance()
        second = None if self.at(":", "]", ",") else self.expression()
        if self.accept(":"):
            third = self.expression()
            return self.finish(ast.Range(start, third, second), start_token)
        return self.finish(ast.Range(start, second), start_token)

    def index_operator(self) -> ast.Index:
        self.expect("[")
        if self.at("{"):
            index: ast.Index = self.discrete_set()
        else:
            index = [self.range_or_expression()]
            while self.accept(","):
                if self.at("]"):
                    break
                index.append(self.range_or_expression())
        self.expect("]")
        return index

    def expression(self, min_power: int = 0) -> ast.Expression:
        start = self.peek()
        left = self.prefix()
        while True:
            kind = self.peek().kind
            if kind == "[":
                index = self.index_operator()
                left = self.finish(ast.IndexExpression(left, index), start)
                continue
            if kind == "^" and self.openqasm2:
                kind = "**"  # '^' is exponentiation in OpenQASM 2
            power = _BINARY_POWER.get(kind)
            if power is None or power < min_power:
                return left
            self.advance()
            # ``**`` is right-associative, everything else left-associative.
            right = self.expression(power if kind == "**" else power + 1)
            left = self.finish(ast.BinaryExpression(ast.BinaryOperator(kind), left, right), start)

    def prefix(self) -> ast.Expression:
        token = self.peek()
        kind = token.kind
        if kind in ("-", "~", "!"):
            self.advance()
            operand = self.expression(_UNARY_POWER)
            return self.finish(ast.UnaryExpression(ast.UnaryOperator(kind), operand), token)
        if kind == "(":
            self.advance()
            expression = self.expression()
            self.expect(")")
            return self.finish(expression, token)
        if kind == "INTEGER":
            self.advance()
            return self.finish(ast.IntegerLiteral(token.value), token)
        if kind == "FLOAT":
            self.advance()
            return self.finish(ast.FloatLiteral(token.value), token)
        if kind == "IMAGINARY":
            self.advance()
            return self.finish(ast.ImaginaryLiteral(token.value), token)
        if kind == "TIMING":
            self.advance()
            value, unit = token.value
            return self.finish(ast.DurationLiteral(value, ast.TimeUnit(unit)), token)
        if kind in ("true", "false"):
            self.advance()
            return self.finish(ast.BooleanLiteral(token.value), token)
        if kind == "STRING":
            self.advance()
            if token.text[0] != '"' or not is_bitstring(token.value):
                raise self.error(f"invalid bit-string literal {token.text}", token.span)
            bits = token.value.replace("_", "")
            return self.finish(ast.BitstringLiteral(int(bits, 2), len(bits)), token)
        if kind == "HARDWARE_QUBIT":
            self.advance()
            return self.finish(ast.HardwareQubit(token.value), token)
        if kind == "IDENTIFIER":
            name = self.identifier()
            if not self.at("("):
                return name
            arguments = self.call_arguments()
            if name.name == "sizeof" and len(arguments) not in (1, 2):
                raise self.error("'sizeof' needs either one or two arguments", token.span)
            return self.finish(ast.FunctionCall(name, arguments), token)
        if kind in TYPE_KEYWORDS:
            type_ = self.array_type() if kind == "array" else self.scalar_type()
            self.expect("(", "'(' of a cast")
            operand = self.expression()
            self.expect(")")
            return self.finish(ast.Cast(type_, operand), token)
        if kind == "durationof":
            self.advance()
            self.expect("(")
            with self.scope(_BLOCK):
                body = self.braced_statements()
            self.expect(")")
            return self.finish(ast.DurationOf(body), token)
        raise self.unexpected("an expression")


def _is_negative_or_zero(expression: ast.Expression) -> bool:
    """Literal sizes that are obviously invalid (``0``, ``-1``)."""
    if isinstance(expression, ast.IntegerLiteral):
        return expression.value == 0
    return isinstance(expression, ast.UnaryExpression) and expression.op is ast.UnaryOperator.NEGATE


_STATEMENT_KEYWORDS: dict[str, Callable[[_Parser], ast.Statement]] = {
    "include": _Parser.include,
    "defcalgrammar": _Parser.defcalgrammar,
    "def": _Parser.subroutine_definition,
    "extern": _Parser.extern,
    "gate": _Parser.gate_definition,
    "cal": _Parser.calibration,
    "defcal": _Parser.defcal,
    "box": _Parser.box,
    "let": _Parser.let,
    "break": _Parser.break_,
    "continue": _Parser.continue_,
    "end": _Parser.end,
    "return": _Parser.return_,
    "if": _Parser.if_,
    "for": _Parser.for_,
    "while": _Parser.while_,
    "switch": _Parser.switch,
    "barrier": _Parser.barrier,
    "delay": _Parser.delay,
    "nop": _Parser.nop,
    "reset": _Parser.reset,
    "measure": _Parser.measure_statement,
    "const": _Parser.const_declaration,
    "input": _Parser.io_declaration,
    "output": _Parser.io_declaration,
    "qubit": _Parser.qubit_declaration,
    "qreg": _Parser.old_style_declaration,
    "creg": _Parser.old_style_declaration,
}


def parse(
    source: str,
    *,
    filename: str | None = None,
    ignore_version: bool = False,
) -> ast.Program:
    """Parse an OpenQASM program into a :class:`~openqasm.ast.Program`.

    Args:
        source: the program text.
        filename: name used in error messages and spans.
        ignore_version: parse programs declaring a version other than 2.0, 3.0
            or 3.1 instead of raising :class:`~openqasm.errors.UnsupportedVersionError`.

    Raises:
        QasmSyntaxError: the program is not syntactically valid.
    """
    tokens, comments = tokenize(source, filename)
    program = _Parser(tokens, filename, ignore_version).program()
    program.comments = comments
    return program


def parse_expression(source: str) -> ast.Expression:
    """Parse a single expression, such as ``"2 * pi / 3"``."""
    tokens, _ = tokenize(source)
    parser = _Parser(tokens, None, ignore_version=True)
    parser.contexts.append([_GLOBAL])
    expression = parser.expression()
    if not parser.at("EOF"):
        raise parser.unexpected("end of expression")
    return expression
