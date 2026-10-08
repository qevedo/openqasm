"""Turn an AST back into OpenQASM source text.

``parse(dumps(program)) == program`` holds for every program produced by the
parser: the printer adds the parentheses that precedence requires and nothing
else. OpenQASM 2 programs (``Program.version`` 2.x) are printed in OpenQASM 2
syntax, so that they stay valid for OpenQASM 2 tools.
"""

from __future__ import annotations

import math
from typing import Any, TextIO

from . import ast

__all__ = ["dumps", "dump", "Printer"]

_BINARY_PRECEDENCE = {
    ast.BinaryOperator.LOGICAL_OR: 1,
    ast.BinaryOperator.LOGICAL_AND: 2,
    ast.BinaryOperator.BIT_OR: 3,
    ast.BinaryOperator.BIT_XOR: 4,
    ast.BinaryOperator.BIT_AND: 5,
    ast.BinaryOperator.EQUAL: 6,
    ast.BinaryOperator.NOT_EQUAL: 6,
    ast.BinaryOperator.LESS: 7,
    ast.BinaryOperator.GREATER: 7,
    ast.BinaryOperator.LESS_EQUAL: 7,
    ast.BinaryOperator.GREATER_EQUAL: 7,
    ast.BinaryOperator.SHIFT_LEFT: 8,
    ast.BinaryOperator.SHIFT_RIGHT: 8,
    ast.BinaryOperator.ADD: 9,
    ast.BinaryOperator.SUBTRACT: 9,
    ast.BinaryOperator.MULTIPLY: 10,
    ast.BinaryOperator.DIVIDE: 10,
    ast.BinaryOperator.MODULO: 10,
    ast.BinaryOperator.POWER: 12,
}
_CONCATENATION = 0
_UNARY = 11
_POSTFIX = 13
_PRIMARY = 14


def _float(value: float) -> str:
    if math.isinf(value):
        return "1.0e999" if value > 0 else "-1.0e999"
    text = repr(float(value))
    return text


class Printer:
    """Configurable printer; :func:`dumps` is the convenient entry point.

    Args:
        indent: the text used for one level of indentation.
        chain_else_if: print ``else { if ... }`` as ``else if ...`` when the
            ``else`` branch is a single ``if`` statement.
        old_measurement: print every measurement with the OpenQASM 2 arrow
            syntax ``measure q -> c;``. Measurements parsed from that syntax are
            always printed with it.
    """

    def __init__(
        self,
        indent: str = "  ",
        chain_else_if: bool = True,
        old_measurement: bool = False,
    ):
        self.indent = indent
        self.chain_else_if = chain_else_if
        self.old_measurement = old_measurement
        self.openqasm2 = False
        self._lines: list[str] = []
        self._depth = 0

    # -- entry points -----------------------------------------------------------

    def dumps(self, node: Any) -> str:
        self._lines = []
        self._depth = 0
        self.openqasm2 = isinstance(node, ast.Program) and (node.version or "").startswith("2")
        if isinstance(node, (ast.Expression, ast.Range, ast.DiscreteSet)):
            return self.expression(node)
        if isinstance(node, ast.Type):
            return self.type(node)
        if isinstance(node, (ast.MeasureExpression, ast.QuantumCall)):
            return self.rvalue(node)
        if isinstance(node, list):
            for statement in node:
                self.statement(statement)
        elif isinstance(node, ast.Program):
            self.program(node)
        else:
            self.statement(node)
        return "".join(self._lines)

    # -- output helpers ---------------------------------------------------------

    def line(self, text: str) -> None:
        """Emit ``text`` at the current depth; nested ``durationof`` bodies may span lines."""
        for part in text.split("\n"):
            self._lines.append(self.indent * self._depth + part + "\n")

    def raw_line(self, text: str) -> None:
        """Emit ``text`` with its inner line breaks untouched (calibration bodies)."""
        self._lines.append(self.indent * self._depth + text + "\n")

    def body(self, statements: list[ast.Statement]) -> None:
        self._depth += 1
        for statement in statements:
            self.statement(statement)
        self._depth -= 1

    def braced(self, header: str, statements: list[ast.Statement], footer: str = "}") -> None:
        self.line(header + " {")
        self.body(statements)
        self.line(footer)

    # -- program and statements -------------------------------------------------

    def program(self, node: ast.Program) -> None:
        if node.version is not None:
            self.line(f"OPENQASM {node.version};")
        for statement in node.statements:
            self.statement(statement)

    def statement(self, node: ast.Statement) -> None:
        for annotation in node.annotations:
            self.line(
                f"@{annotation.keyword}" + (f" {annotation.command}" if annotation.command else "")
            )
        method = getattr(self, f"statement_{type(node).__name__}", None)
        if method is None:
            raise TypeError(f"cannot print {type(node).__name__}")
        method(node)

    def statement_Pragma(self, node: ast.Pragma) -> None:
        self.line(f"pragma {node.command}")

    def statement_Include(self, node: ast.Include) -> None:
        self.line(f'include "{node.filename}";')

    def statement_CalibrationGrammar(self, node: ast.CalibrationGrammar) -> None:
        self.line(f'defcalgrammar "{node.name}";')

    def statement_Block(self, node: ast.Block) -> None:
        self.line("{")
        self.body(node.statements)
        self.line("}")

    def statement_ExpressionStatement(self, node: ast.ExpressionStatement) -> None:
        self.line(f"{self.expression(node.expression)};")

    def statement_QubitDeclaration(self, node: ast.QubitDeclaration) -> None:
        name = node.name.name
        if node.legacy_syntax or self.openqasm2:
            size = f"[{self.expression(node.size)}]" if node.size is not None else ""
            self.line(f"qreg {name}{size};")
        else:
            size = f"[{self.expression(node.size)}]" if node.size is not None else ""
            self.line(f"qubit{size} {name};")

    def statement_ClassicalDeclaration(self, node: ast.ClassicalDeclaration) -> None:
        name = node.name.name
        if (
            (node.legacy_syntax or self.openqasm2)
            and isinstance(node.type, ast.BitType)
            and node.init is None
        ):
            size = f"[{self.expression(node.type.size)}]" if node.type.size is not None else ""
            self.line(f"creg {name}{size};")
            return
        text = f"{self.type(node.type)} {name}"
        if node.init is not None:
            text += f" = {self.rvalue(node.init)}"
        self.line(text + ";")

    def statement_ConstDeclaration(self, node: ast.ConstDeclaration) -> None:
        self.line(f"const {self.type(node.type)} {node.name.name} = {self.rvalue(node.init)};")

    def statement_IODeclaration(self, node: ast.IODeclaration) -> None:
        self.line(f"{node.io.value} {self.type(node.type)} {node.name.name};")

    def statement_AliasDeclaration(self, node: ast.AliasDeclaration) -> None:
        self.line(f"let {node.name.name} = {self.expression(node.value)};")

    def statement_Assignment(self, node: ast.Assignment) -> None:
        self.line(f"{self.operand(node.target)} {node.op.value} {self.rvalue(node.value)};")

    def statement_GateDefinition(self, node: ast.GateDefinition) -> None:
        header = f"gate {node.name.name}"
        if node.parameters:
            header += f"({', '.join(p.name for p in node.parameters)})"
        header += " " + ", ".join(q.name for q in node.qubits)
        self.braced(header, node.body)

    def statement_OpaqueDefinition(self, node: ast.OpaqueDefinition) -> None:
        text = f"opaque {node.name.name}"
        if node.parameters:
            text += f"({', '.join(p.name for p in node.parameters)})"
        self.line(text + " " + ", ".join(q.name for q in node.qubits) + ";")

    def modifiers(self, modifiers: list[ast.GateModifier]) -> str:
        out = []
        for modifier in modifiers:
            text = modifier.kind.value
            if modifier.argument is not None:
                text += f"({self.expression(modifier.argument)})"
            out.append(text + " @ ")
        return "".join(out)

    def statement_GateCall(self, node: ast.GateCall) -> None:
        text = self.modifiers(node.modifiers) + node.name.name
        if node.arguments:
            text += f"({', '.join(self.expression(a) for a in node.arguments)})"
        if node.duration is not None:
            text += f"[{self.expression(node.duration)}]"
        text += " " + ", ".join(self.operand(q) for q in node.qubits)
        self.line(text + ";")

    def statement_GlobalPhase(self, node: ast.GlobalPhase) -> None:
        text = self.modifiers(node.modifiers) + f"gphase({self.expression(node.argument)})"
        if node.duration is not None:
            text += f"[{self.expression(node.duration)}]"
        if node.qubits:
            text += " " + ", ".join(self.operand(q) for q in node.qubits)
        self.line(text + ";")

    def statement_MeasureStatement(self, node: ast.MeasureStatement) -> None:
        measure = self.rvalue(node.measure)
        if node.target is None:
            self.line(f"{measure};")
        elif node.arrow_syntax or self.old_measurement or self.openqasm2:
            self.line(f"{measure} -> {self.operand(node.target)};")
        else:
            self.line(f"{self.operand(node.target)} = {measure};")

    def statement_Reset(self, node: ast.Reset) -> None:
        self.line(f"reset {self.operand(node.operand)};")

    def operand_list(self, keyword: str, operands: list[ast.Operand]) -> str:
        if not operands:
            return keyword
        return keyword + " " + ", ".join(self.operand(o) for o in operands)

    def statement_Barrier(self, node: ast.Barrier) -> None:
        self.line(self.operand_list("barrier", node.operands) + ";")

    def statement_Nop(self, node: ast.Nop) -> None:
        self.line(self.operand_list("nop", node.operands) + ";")

    def statement_Delay(self, node: ast.Delay) -> None:
        self.line(
            self.operand_list(f"delay[{self.expression(node.duration)}]", node.operands) + ";"
        )

    def statement_Box(self, node: ast.Box) -> None:
        header = "box" if node.duration is None else f"box[{self.expression(node.duration)}]"
        self.braced(header, node.body)

    def argument(self, node: ast.Node) -> str:
        if isinstance(node, ast.QuantumArgument):
            size = "" if node.size is None else f"[{self.expression(node.size)}]"
            if node.legacy_syntax:
                return f"qreg {node.name.name}{size}"
            return f"qubit{size} {node.name.name}"
        if isinstance(node, ast.ClassicalArgument):
            if node.legacy_syntax and isinstance(node.type, ast.BitType):
                size = "" if node.type.size is None else f"[{self.expression(node.type.size)}]"
                return f"creg {node.name.name}{size}"
            return f"{self.type(node.type)} {node.name.name}"
        if isinstance(node, ast.ExternArgument):
            if node.legacy_syntax and isinstance(node.type, ast.BitType):
                size = "" if node.type.size is None else f"[{self.expression(node.type.size)}]"
                return f"creg{size}"
            return self.type(node.type)
        return self.expression(node)

    def return_signature(self, return_type: ast.ScalarType | None) -> str:
        return "" if return_type is None else f" -> {self.type(return_type)}"

    def statement_SubroutineDefinition(self, node: ast.SubroutineDefinition) -> None:
        arguments = ", ".join(self.argument(a) for a in node.arguments)
        header = f"def {node.name.name}({arguments}){self.return_signature(node.return_type)}"
        self.braced(header, node.body)

    def statement_ExternDeclaration(self, node: ast.ExternDeclaration) -> None:
        arguments = ", ".join(self.argument(a) for a in node.arguments)
        self.line(f"extern {node.name.name}({arguments}){self.return_signature(node.return_type)};")

    def statement_Return(self, node: ast.Return) -> None:
        if node.value is None:
            self.line("return;")
        else:
            self.line(f"return {self.rvalue(node.value)};")

    def statement_Break(self, node: ast.Break) -> None:
        self.line("break;")

    def statement_Continue(self, node: ast.Continue) -> None:
        self.line("continue;")

    def statement_End(self, node: ast.End) -> None:
        self.line("end;")

    def statement_If(self, node: ast.If) -> None:
        condition = self.expression(node.condition)
        if self.openqasm2 and len(node.then_body) == 1 and not node.else_body:
            # OpenQASM 2 has no blocks: 'if (c == 1) x q;'.
            nested = Printer(self.indent, self.chain_else_if, self.old_measurement)
            nested.openqasm2 = True
            nested.statement(node.then_body[0])
            self.line(f"if ({condition}) {''.join(nested._lines).strip()}")
            return
        self.line(f"if ({condition}) {{")
        self.body(node.then_body)
        else_body = node.else_body
        while (
            self.chain_else_if
            and len(else_body) == 1
            and isinstance(else_body[0], ast.If)
            and not else_body[0].annotations
        ):
            chained = else_body[0]
            self.line(f"}} else if ({self.expression(chained.condition)}) {{")
            self.body(chained.then_body)
            else_body = chained.else_body
        if else_body:
            self.line("} else {")
            self.body(else_body)
        self.line("}")

    def statement_While(self, node: ast.While) -> None:
        self.braced(f"while ({self.expression(node.condition)})", node.body)

    def statement_For(self, node: ast.For) -> None:
        if isinstance(node.iterable, ast.Range):
            iterable = f"[{self.range(node.iterable)}]"
        else:
            iterable = self.expression(node.iterable)
        header = f"for {self.type(node.type)} {node.variable.name} in {iterable}"
        self.braced(header, node.body)

    def statement_Switch(self, node: ast.Switch) -> None:
        self.line(f"switch ({self.expression(node.target)}) {{")
        self._depth += 1
        for case in node.cases:
            values = ", ".join(self.expression(v) for v in case.values)
            self.braced(f"case {values}", case.body)
        if node.default is not None:
            self.braced("default", node.default)
        self._depth -= 1
        self.line("}")

    def statement_CalibrationBlock(self, node: ast.CalibrationBlock) -> None:
        self.raw_line(f"cal {{{node.body}}}")

    def statement_CalibrationDefinition(self, node: ast.CalibrationDefinition) -> None:
        text = f"defcal {node.name.name}"
        if node.arguments:
            text += f"({', '.join(self.argument(a) for a in node.arguments)})"
        text += " " + ", ".join(self.expression(o) for o in node.operands)
        text += self.return_signature(node.return_type)
        self.raw_line(f"{text} {{{node.body}}}")

    # -- types ------------------------------------------------------------------

    def type(self, node: ast.Type) -> str:
        if isinstance(node, (ast.IntType, ast.UintType, ast.FloatType, ast.AngleType, ast.BitType)):
            name = {
                ast.IntType: "int",
                ast.UintType: "uint",
                ast.FloatType: "float",
                ast.AngleType: "angle",
                ast.BitType: "bit",
            }[type(node)]
            return name if node.size is None else f"{name}[{self.expression(node.size)}]"
        if isinstance(node, ast.BoolType):
            return "bool"
        if isinstance(node, ast.DurationType):
            return "duration"
        if isinstance(node, ast.StretchType):
            return "stretch"
        if isinstance(node, ast.ComplexType):
            return "complex" if node.base is None else f"complex[{self.type(node.base)}]"
        if isinstance(node, ast.ArrayType):
            dimensions = ", ".join(self.expression(d) for d in node.dimensions)
            return f"array[{self.type(node.base)}, {dimensions}]"
        if isinstance(node, ast.ArrayReferenceType):
            if node.rank is not None:
                dimensions = f"#dim={self.expression(node.rank)}"
            else:
                dimensions = ", ".join(self.expression(d) for d in node.dimensions or [])
            return f"{node.access.value} array[{self.type(node.base)}, {dimensions}]"
        if isinstance(node, ast.QubitType):
            return "qubit" if node.size is None else f"qubit[{self.expression(node.size)}]"
        raise TypeError(f"cannot print type {type(node).__name__}")

    # -- expressions ------------------------------------------------------------

    def rvalue(self, node: Any) -> str:
        if isinstance(node, ast.MeasureExpression):
            return f"measure {self.operand(node.operand)}"
        if isinstance(node, ast.QuantumCall):
            text = node.name.name
            if node.arguments:
                text += f"({', '.join(self.expression(a) for a in node.arguments)})"
            return text + " " + ", ".join(self.operand(o) for o in node.operands)
        return self.expression(node)

    def operand(self, node: Any) -> str:
        if isinstance(node, ast.IndexedIdentifier):
            return node.name.name + "".join(f"[{self.index(i)}]" for i in node.indices)
        return self.expression(node)

    def index(self, index: ast.Index) -> str:
        if isinstance(index, ast.DiscreteSet):
            return self.expression(index)
        return ", ".join(
            self.range(i) if isinstance(i, ast.Range) else self.expression(i) for i in index
        )

    def range(self, node: ast.Range) -> str:
        parts = ["" if node.start is None else self.expression(node.start)]
        if node.step is not None:
            parts.append(self.expression(node.step))
        parts.append("" if node.end is None else self.expression(node.end))
        return ":".join(parts)

    def precedence(self, node: Any) -> int:
        if isinstance(node, ast.BinaryExpression):
            return _BINARY_PRECEDENCE[node.op]
        if isinstance(node, ast.UnaryExpression):
            return _UNARY
        if isinstance(node, ast.Concatenation):
            return _CONCATENATION
        if isinstance(node, (ast.IntegerLiteral, ast.FloatLiteral, ast.ImaginaryLiteral)):
            return _UNARY if node.value < 0 else _PRIMARY
        if isinstance(node, ast.DurationLiteral):
            return _UNARY if node.value < 0 else _PRIMARY
        if isinstance(node, ast.IndexExpression):
            return _POSTFIX
        return _PRIMARY

    def wrapped(self, node: Any, minimum: int) -> str:
        text = self.expression(node)
        return f"({text})" if self.precedence(node) < minimum else text

    def expression(self, node: Any) -> str:
        method = getattr(self, f"expression_{type(node).__name__}", None)
        if method is None:
            raise TypeError(f"cannot print {type(node).__name__}")
        return method(node)

    def expression_Identifier(self, node: ast.Identifier) -> str:
        return node.name

    def expression_HardwareQubit(self, node: ast.HardwareQubit) -> str:
        return node.name

    def expression_IntegerLiteral(self, node: ast.IntegerLiteral) -> str:
        return str(node.value)

    def expression_FloatLiteral(self, node: ast.FloatLiteral) -> str:
        return _float(node.value)

    def expression_ImaginaryLiteral(self, node: ast.ImaginaryLiteral) -> str:
        return _float(node.value) + "im"

    def expression_BooleanLiteral(self, node: ast.BooleanLiteral) -> str:
        return "true" if node.value else "false"

    def expression_BitstringLiteral(self, node: ast.BitstringLiteral) -> str:
        return f'"{node.bits}"'

    def expression_DurationLiteral(self, node: ast.DurationLiteral) -> str:
        return _float(node.value) + node.unit.value

    def expression_ArrayLiteral(self, node: ast.ArrayLiteral) -> str:
        return "{" + ", ".join(self.expression(v) for v in node.values) + "}"

    def expression_DiscreteSet(self, node: ast.DiscreteSet) -> str:
        return "{" + ", ".join(self.expression(v) for v in node.values) + "}"

    def expression_Range(self, node: ast.Range) -> str:
        return self.range(node)

    def expression_UnaryExpression(self, node: ast.UnaryExpression) -> str:
        return node.op.value + self.wrapped(node.operand, _UNARY)

    def expression_BinaryExpression(self, node: ast.BinaryExpression) -> str:
        precedence = _BINARY_PRECEDENCE[node.op]
        right_associative = node.op is ast.BinaryOperator.POWER
        lhs = self.wrapped(node.lhs, precedence + 1 if right_associative else precedence)
        rhs = self.wrapped(node.rhs, precedence if right_associative else precedence + 1)
        op = "^" if self.openqasm2 and right_associative else node.op.value
        return f"{lhs} {op} {rhs}"

    def expression_Concatenation(self, node: ast.Concatenation) -> str:
        lhs = self.wrapped(node.lhs, _CONCATENATION)
        return f"{lhs} ++ {self.wrapped(node.rhs, _CONCATENATION + 1)}"

    def expression_Cast(self, node: ast.Cast) -> str:
        return f"{self.type(node.type)}({self.expression(node.operand)})"

    def expression_FunctionCall(self, node: ast.FunctionCall) -> str:
        return f"{node.name.name}({', '.join(self.expression(a) for a in node.arguments)})"

    def expression_IndexExpression(self, node: ast.IndexExpression) -> str:
        return f"{self.wrapped(node.collection, _POSTFIX)}[{self.index(node.index)}]"

    def expression_DurationOf(self, node: ast.DurationOf) -> str:
        nested = Printer(self.indent, self.chain_else_if, self.old_measurement)
        nested._depth = 1
        for statement in node.body:
            nested.statement(statement)
        return "durationof({\n" + "".join(nested._lines) + "})"


def dumps(
    node: Any,
    *,
    indent: str = "  ",
    chain_else_if: bool = True,
    old_measurement: bool = False,
) -> str:
    """Return the OpenQASM source of ``node`` (a program, statement, expression or type)."""
    return Printer(indent, chain_else_if, old_measurement).dumps(node)


def dump(node: Any, file: TextIO, **options: Any) -> None:
    """Write the OpenQASM source of ``node`` to an open text file."""
    file.write(dumps(node, **options))
