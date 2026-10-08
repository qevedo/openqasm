"""Translate between :mod:`openqasm.ast` and the reference ``openqasm3.ast``.

The OpenQASM project publishes a reference Python package, ``openqasm3``,
with its own AST and an extensive test suite. This adapter lets that suite
exercise *our* parser and printer: it converts our trees to reference trees
(for the parser tests) and reference trees to ours (for the printer tests).

Spans follow the reference conventions: columns start at 0 and the end
column is the start of the last token of a grammar rule, or the last
character of a single token.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import openqasm3.ast as ref

import openqasm
from openqasm import ast
from openqasm.lexer import tokenize

__all__ = ["to_reference", "from_reference", "ReferenceSpans"]


class ReferenceSpans:
    """Convert our spans (exclusive end) to reference spans for one source text."""

    def __init__(self, source: str | None):
        self.token_starts: dict[tuple[int, int], tuple[int, int]] = {}
        if source is not None:
            try:
                tokens, _ = tokenize(source)
            except openqasm.QasmError:
                tokens = []
            for token in tokens:
                if token.kind == "EOF":
                    continue
                key = (token.span.end_line, token.span.end_column)
                self.token_starts[key] = (token.span.start_line, token.span.start_column)

    def rule(self, span: ast.Span | None) -> ref.Span | None:
        if span is None:
            return None
        end = self.token_starts.get((span.end_line, span.end_column))
        if end is None:
            end = (span.end_line, max(span.start_column, span.end_column - 1))
        return ref.Span(span.start_line, span.start_column, end[0], end[1])

    def terminal(self, span: ast.Span | None) -> ref.Span | None:
        if span is None:
            return None
        return ref.Span(span.start_line, span.start_column, span.end_line, span.end_column - 1)


class _ToReference:
    def __init__(self, source: str | None):
        self.spans = ReferenceSpans(source)

    def __call__(self, node: Any) -> Any:
        return self.convert(node)

    def with_span(self, out: ref.QASMNode, node: ast.Node) -> Any:
        out.span = self.spans.rule(node.span)
        return out

    def name(self, identifier: ast.Identifier) -> ref.Identifier:
        """Identifiers in name positions are single tokens in the reference."""
        out = ref.Identifier(identifier.name)
        out.span = self.spans.terminal(identifier.span)
        return out

    def convert(self, node: Any) -> Any:
        if node is None:
            return None
        if isinstance(node, list):
            return [self.convert(item) for item in node]
        method = getattr(self, f"convert_{type(node).__name__}")
        out = method(node)
        if isinstance(out, ref.QASMNode) and out.span is None and isinstance(node, ast.Node):
            self.with_span(out, node)
        if isinstance(node, ast.Statement) and isinstance(out, ref.Statement):
            out.annotations = self.convert(node.annotations)
        return out

    def body(self, statements: list[ast.Statement], owner: ast.Node) -> ref.CompoundStatement:
        out = ref.CompoundStatement(self.convert(statements))
        return self.with_span(out, owner)

    # program structure
    def convert_Program(self, node: ast.Program) -> ref.Program:
        return ref.Program(self.convert(node.statements), node.version)

    def convert_Annotation(self, node):
        return ref.Annotation(node.keyword, node.command)

    def convert_Pragma(self, node):
        return ref.Pragma(node.command)

    def convert_Include(self, node):
        return ref.Include(node.filename)

    def convert_CalibrationGrammar(self, node):
        return ref.CalibrationGrammarDeclaration(node.name)

    def convert_Block(self, node):
        return ref.CompoundStatement(self.convert(node.statements))

    def convert_ExpressionStatement(self, node):
        return ref.ExpressionStatement(self.convert(node.expression))

    # declarations
    def convert_QubitDeclaration(self, node):
        return ref.QubitDeclaration(self.name(node.name), self.convert(node.size))

    def convert_ClassicalDeclaration(self, node):
        return ref.ClassicalDeclaration(
            self.convert(node.type), self.name(node.name), self.convert(node.init)
        )

    def convert_ConstDeclaration(self, node):
        return ref.ConstantDeclaration(
            self.convert(node.type), self.name(node.name), self.convert(node.init)
        )

    def convert_IODeclaration(self, node):
        return ref.IODeclaration(
            ref.IOKeyword[node.io.value], self.convert(node.type), self.name(node.name)
        )

    def convert_AliasDeclaration(self, node):
        return ref.AliasStatement(self.name(node.name), self.convert(node.value))

    def convert_Assignment(self, node):
        if isinstance(node.value, (ast.MeasureExpression, ast.QuantumCall)):
            return ref.QuantumMeasurementStatement(
                self.convert(node.value), self.convert(node.target)
            )
        return ref.ClassicalAssignment(
            self.convert(node.target),
            ref.AssignmentOperator[node.op.value],
            self.convert(node.value),
        )

    # gates and quantum instructions
    def convert_GateModifier(self, node):
        return ref.QuantumGateModifier(
            ref.GateModifierName[node.kind.value], self.convert(node.argument)
        )

    def convert_GateDefinition(self, node):
        return ref.QuantumGateDefinition(
            self.name(node.name),
            [self.name(p) for p in node.parameters],
            [self.name(q) for q in node.qubits],
            self.convert(node.body),
        )

    def convert_GateCall(self, node):
        return ref.QuantumGate(
            self.convert(node.modifiers),
            self.name(node.name),
            self.convert(node.arguments),
            self.convert(node.qubits),
            self.convert(node.duration),
        )

    def convert_GlobalPhase(self, node):
        return ref.QuantumPhase(
            self.convert(node.modifiers), self.convert(node.argument), self.convert(node.qubits)
        )

    def convert_MeasureStatement(self, node):
        return ref.QuantumMeasurementStatement(
            self.convert(node.measure), self.convert(node.target)
        )

    def convert_MeasureExpression(self, node):
        return ref.QuantumMeasurement(self.convert(node.operand))

    def convert_QuantumCall(self, node):
        return ref.QuantumCallExpression(
            self.name(node.name), self.convert(node.arguments), self.convert(node.operands)
        )

    def convert_Reset(self, node):
        return ref.QuantumReset(self.convert(node.operand))

    def convert_Barrier(self, node):
        return ref.QuantumBarrier(self.convert(node.operands))

    def convert_Delay(self, node):
        return ref.DelayInstruction(self.convert(node.duration), self.convert(node.operands))

    def convert_Nop(self, node):
        return ref.QuantumNop(self.convert(node.operands))

    def convert_Box(self, node):
        return ref.Box(self.convert(node.duration), self.convert(node.body))

    # subroutines
    def convert_ClassicalArgument(self, node):
        access = None
        if isinstance(node.type, ast.ArrayReferenceType):
            access = ref.AccessControl[node.type.access.value]
        return ref.ClassicalArgument(self.convert(node.type), self.name(node.name), access)

    def convert_QuantumArgument(self, node):
        return ref.QuantumArgument(self.name(node.name), self.convert(node.size))

    def convert_SubroutineDefinition(self, node):
        return ref.SubroutineDefinition(
            self.name(node.name),
            self.convert(node.arguments),
            self.convert(node.body),
            self.convert(node.return_type),
        )

    def convert_ExternArgument(self, node):
        access = None
        if isinstance(node.type, ast.ArrayReferenceType):
            access = ref.AccessControl[node.type.access.value]
        return ref.ExternArgument(self.convert(node.type), access)

    def convert_ExternDeclaration(self, node):
        return ref.ExternDeclaration(
            self.name(node.name), self.convert(node.arguments), self.convert(node.return_type)
        )

    def convert_Return(self, node):
        return ref.ReturnStatement(self.convert(node.value))

    def convert_Break(self, node):
        return ref.BreakStatement()

    def convert_Continue(self, node):
        return ref.ContinueStatement()

    def convert_End(self, node):
        return ref.EndStatement()

    # control flow
    def convert_If(self, node):
        return ref.BranchingStatement(
            self.convert(node.condition), self.convert(node.then_body), self.convert(node.else_body)
        )

    def convert_While(self, node):
        return ref.WhileLoop(self.convert(node.condition), self.convert(node.body))

    def convert_For(self, node):
        return ref.ForInLoop(
            self.convert(node.type),
            self.name(node.variable),
            self.convert(node.iterable),
            self.convert(node.body),
        )

    def convert_Switch(self, node):
        cases = [(self.convert(case.values), self.body(case.body, case)) for case in node.cases]
        default = None if node.default is None else self.body(node.default, node)
        return ref.SwitchStatement(self.convert(node.target), cases, default)

    # calibration
    def convert_CalibrationBlock(self, node):
        return ref.CalibrationStatement(node.body)

    def convert_CalibrationDefinition(self, node):
        return ref.CalibrationDefinition(
            self.with_span(ref.Identifier(node.name.name), node.name),
            self.convert(node.arguments),
            [
                self.name(o) if isinstance(o, ast.Identifier) else self.convert(o)
                for o in node.operands
            ],
            self.convert(node.return_type),
            node.body,
        )

    def convert_OpaqueDefinition(self, node):
        raise TypeError("the reference AST has no OpenQASM 2 'opaque' statement")

    # expressions
    def convert_Identifier(self, node):
        return ref.Identifier(node.name)

    def convert_HardwareQubit(self, node):
        return ref.Identifier(node.name)

    def convert_IntegerLiteral(self, node):
        return ref.IntegerLiteral(node.value)

    def convert_FloatLiteral(self, node):
        return ref.FloatLiteral(node.value)

    def convert_ImaginaryLiteral(self, node):
        return ref.ImaginaryLiteral(node.value)

    def convert_BooleanLiteral(self, node):
        return ref.BooleanLiteral(node.value)

    def convert_BitstringLiteral(self, node):
        return ref.BitstringLiteral(node.value, node.width)

    def convert_DurationLiteral(self, node):
        return ref.DurationLiteral(node.value, ref.TimeUnit[node.unit.value])

    def convert_ArrayLiteral(self, node):
        return ref.ArrayLiteral(self.convert(node.values))

    def convert_UnaryExpression(self, node):
        return ref.UnaryExpression(ref.UnaryOperator[node.op.value], self.convert(node.operand))

    def convert_BinaryExpression(self, node):
        return ref.BinaryExpression(
            ref.BinaryOperator[node.op.value], self.convert(node.lhs), self.convert(node.rhs)
        )

    def convert_Cast(self, node):
        return ref.Cast(self.convert(node.type), self.convert(node.operand))

    def convert_FunctionCall(self, node):
        if node.name.name == "sizeof":
            arguments = self.convert(node.arguments)
            return ref.SizeOf(arguments[0], arguments[1] if len(arguments) > 1 else None)
        return ref.FunctionCall(self.name(node.name), self.convert(node.arguments))

    def convert_IndexExpression(self, node):
        return ref.IndexExpression(self.convert(node.collection), self.convert(node.index))

    def convert_Concatenation(self, node):
        return ref.Concatenation(self.convert(node.lhs), self.convert(node.rhs))

    def convert_DurationOf(self, node):
        return ref.DurationOf(self.convert(node.body))

    def convert_Range(self, node):
        return ref.RangeDefinition(
            self.convert(node.start), self.convert(node.end), self.convert(node.step)
        )

    def convert_DiscreteSet(self, node):
        return ref.DiscreteSet(self.convert(node.values))

    def convert_IndexedIdentifier(self, node):
        return ref.IndexedIdentifier(self.name(node.name), self.convert(node.indices))

    # types
    def convert_IntType(self, node):
        return ref.IntType(self.convert(node.size))

    def convert_UintType(self, node):
        return ref.UintType(self.convert(node.size))

    def convert_FloatType(self, node):
        return ref.FloatType(self.convert(node.size))

    def convert_AngleType(self, node):
        return ref.AngleType(self.convert(node.size))

    def convert_BitType(self, node):
        return ref.BitType(self.convert(node.size))

    def convert_BoolType(self, node):
        return ref.BoolType()

    def convert_ComplexType(self, node):
        return ref.ComplexType(self.convert(node.base))

    def convert_DurationType(self, node):
        return ref.DurationType()

    def convert_StretchType(self, node):
        return ref.StretchType()

    def convert_ArrayType(self, node):
        return ref.ArrayType(self.convert(node.base), self.convert(node.dimensions))

    def convert_ArrayReferenceType(self, node):
        dimensions = self.convert(node.rank if node.dimensions is None else node.dimensions)
        return ref.ArrayReferenceType(self.convert(node.base), dimensions)


def to_reference(node: Any, source: str | None = None) -> Any:
    """Convert one of our nodes (or a list of them) to the reference AST."""
    return _ToReference(source)(node)


# ---------------------------------------------------------------------------
# reference -> ours
# ---------------------------------------------------------------------------


def _ident(node: ref.Identifier) -> ast.Expression:
    if node.name.startswith("$") and node.name[1:].isdigit():
        return ast.HardwareQubit(int(node.name[1:]))
    return ast.Identifier(node.name)


def _name(node: ref.Identifier) -> ast.Identifier:
    return ast.Identifier(node.name)


def _statements(node: Any) -> list[ast.Statement]:
    if isinstance(node, ref.CompoundStatement):
        return from_reference(node.statements)
    return from_reference(node)


_FROM: dict[type, Any] = {
    ref.Program: lambda n: ast.Program(from_reference(n.statements), n.version),
    ref.Annotation: lambda n: ast.Annotation(n.keyword, n.command),
    ref.Pragma: lambda n: ast.Pragma(n.command),
    ref.Include: lambda n: ast.Include(n.filename),
    ref.CalibrationGrammarDeclaration: lambda n: ast.CalibrationGrammar(n.name),
    ref.CompoundStatement: lambda n: ast.Block(from_reference(n.statements)),
    ref.ExpressionStatement: lambda n: ast.ExpressionStatement(from_reference(n.expression)),
    ref.QubitDeclaration: lambda n: ast.QubitDeclaration(_name(n.qubit), from_reference(n.size)),
    ref.ClassicalDeclaration: lambda n: ast.ClassicalDeclaration(
        from_reference(n.type), _name(n.identifier), from_reference(n.init_expression)
    ),
    ref.ConstantDeclaration: lambda n: ast.ConstDeclaration(
        from_reference(n.type), _name(n.identifier), from_reference(n.init_expression)
    ),
    ref.IODeclaration: lambda n: ast.IODeclaration(
        ast.IOKeyword(n.io_identifier.name), from_reference(n.type), _name(n.identifier)
    ),
    ref.AliasStatement: lambda n: ast.AliasDeclaration(_name(n.target), from_reference(n.value)),
    ref.ClassicalAssignment: lambda n: ast.Assignment(
        from_reference(n.lvalue), ast.AssignmentOperator(n.op.name), from_reference(n.rvalue)
    ),
    ref.QuantumGateModifier: lambda n: ast.GateModifier(
        ast.GateModifierKind(n.modifier.name), from_reference(n.argument)
    ),
    ref.QuantumGateDefinition: lambda n: ast.GateDefinition(
        _name(n.name),
        [_name(a) for a in n.arguments],
        [_name(q) for q in n.qubits],
        from_reference(n.body),
    ),
    ref.QuantumGate: lambda n: ast.GateCall(
        from_reference(n.modifiers),
        _name(n.name),
        from_reference(n.arguments),
        from_reference(n.qubits),
        from_reference(n.duration),
    ),
    ref.QuantumPhase: lambda n: ast.GlobalPhase(
        from_reference(n.modifiers), from_reference(n.argument), from_reference(n.qubits)
    ),
    ref.QuantumMeasurementStatement: lambda n: ast.MeasureStatement(
        from_reference(n.measure), from_reference(n.target)
    ),
    ref.QuantumMeasurement: lambda n: ast.MeasureExpression(from_reference(n.qubit)),
    ref.QuantumCallExpression: lambda n: ast.QuantumCall(
        _name(n.name), from_reference(n.arguments), from_reference(n.qubits)
    ),
    ref.QuantumReset: lambda n: ast.Reset(from_reference(n.qubits)),
    ref.QuantumBarrier: lambda n: ast.Barrier(from_reference(n.qubits)),
    ref.DelayInstruction: lambda n: ast.Delay(from_reference(n.duration), from_reference(n.qubits)),
    ref.QuantumNop: lambda n: ast.Nop(from_reference(n.operands)),
    ref.Box: lambda n: ast.Box(from_reference(n.duration), from_reference(n.body)),
    ref.ClassicalArgument: lambda n: ast.ClassicalArgument(
        _with_access(from_reference(n.type), n.access), _name(n.name)
    ),
    ref.QuantumArgument: lambda n: ast.QuantumArgument(_name(n.name), from_reference(n.size)),
    ref.SubroutineDefinition: lambda n: ast.SubroutineDefinition(
        _name(n.name),
        from_reference(n.arguments),
        from_reference(n.body),
        from_reference(n.return_type),
    ),
    ref.ExternArgument: lambda n: ast.ExternArgument(
        _with_access(from_reference(n.type), n.access)
    ),
    ref.ExternDeclaration: lambda n: ast.ExternDeclaration(
        _name(n.name), from_reference(n.arguments), from_reference(n.return_type)
    ),
    ref.ReturnStatement: lambda n: ast.Return(from_reference(n.expression)),
    ref.BreakStatement: lambda n: ast.Break(),
    ref.ContinueStatement: lambda n: ast.Continue(),
    ref.EndStatement: lambda n: ast.End(),
    ref.BranchingStatement: lambda n: ast.If(
        from_reference(n.condition), from_reference(n.if_block), from_reference(n.else_block)
    ),
    ref.WhileLoop: lambda n: ast.While(from_reference(n.while_condition), from_reference(n.block)),
    ref.ForInLoop: lambda n: ast.For(
        from_reference(n.type),
        _name(n.identifier),
        from_reference(n.set_declaration),
        from_reference(n.block),
    ),
    ref.SwitchStatement: lambda n: ast.Switch(
        from_reference(n.target),
        [ast.SwitchCase(from_reference(values), _statements(body)) for values, body in n.cases],
        None if n.default is None else _statements(n.default),
    ),
    ref.CalibrationStatement: lambda n: ast.CalibrationBlock(n.body),
    ref.CalibrationDefinition: lambda n: ast.CalibrationDefinition(
        _name(n.name),
        from_reference(n.arguments),
        [_ident(q) for q in n.qubits],
        n.body,
        from_reference(n.return_type),
    ),
    ref.Identifier: _ident,
    ref.IntegerLiteral: lambda n: ast.IntegerLiteral(n.value),
    ref.FloatLiteral: lambda n: ast.FloatLiteral(n.value),
    ref.ImaginaryLiteral: lambda n: ast.ImaginaryLiteral(n.value),
    ref.BooleanLiteral: lambda n: ast.BooleanLiteral(n.value),
    ref.BitstringLiteral: lambda n: ast.BitstringLiteral(n.value, n.width),
    ref.DurationLiteral: lambda n: ast.DurationLiteral(n.value, ast.TimeUnit(n.unit.name)),
    ref.ArrayLiteral: lambda n: ast.ArrayLiteral(from_reference(n.values)),
    ref.UnaryExpression: lambda n: ast.UnaryExpression(
        ast.UnaryOperator(n.op.name), from_reference(n.expression)
    ),
    ref.BinaryExpression: lambda n: ast.BinaryExpression(
        ast.BinaryOperator(n.op.name), from_reference(n.lhs), from_reference(n.rhs)
    ),
    ref.Cast: lambda n: ast.Cast(from_reference(n.type), from_reference(n.argument)),
    ref.FunctionCall: lambda n: ast.FunctionCall(_name(n.name), from_reference(n.arguments)),
    ref.SizeOf: lambda n: ast.FunctionCall(
        ast.Identifier("sizeof"),
        [from_reference(n.target)] + ([] if n.index is None else [from_reference(n.index)]),
    ),
    ref.IndexExpression: lambda n: ast.IndexExpression(
        from_reference(n.collection), from_reference(n.index)
    ),
    ref.Concatenation: lambda n: ast.Concatenation(from_reference(n.lhs), from_reference(n.rhs)),
    ref.DurationOf: lambda n: ast.DurationOf(from_reference(n.target)),
    ref.RangeDefinition: lambda n: ast.Range(
        from_reference(n.start), from_reference(n.end), from_reference(n.step)
    ),
    ref.DiscreteSet: lambda n: ast.DiscreteSet(from_reference(n.values)),
    ref.IndexedIdentifier: lambda n: ast.IndexedIdentifier(
        _name(n.name), from_reference(n.indices)
    ),
    ref.IntType: lambda n: ast.IntType(from_reference(n.size)),
    ref.UintType: lambda n: ast.UintType(from_reference(n.size)),
    ref.FloatType: lambda n: ast.FloatType(from_reference(n.size)),
    ref.AngleType: lambda n: ast.AngleType(from_reference(n.size)),
    ref.BitType: lambda n: ast.BitType(from_reference(n.size)),
    ref.BoolType: lambda n: ast.BoolType(),
    ref.ComplexType: lambda n: ast.ComplexType(from_reference(n.base_type)),
    ref.DurationType: lambda n: ast.DurationType(),
    ref.StretchType: lambda n: ast.StretchType(),
    ref.ArrayType: lambda n: ast.ArrayType(
        from_reference(n.base_type), from_reference(n.dimensions)
    ),
    ref.ArrayReferenceType: lambda n: _array_reference(n),
}


def _array_reference(node: ref.ArrayReferenceType) -> ast.ArrayReferenceType:
    base = from_reference(node.base_type)
    if isinstance(node.dimensions, list):
        return ast.ArrayReferenceType(
            ast.AccessControl.READONLY, base, dimensions=from_reference(node.dimensions)
        )
    return ast.ArrayReferenceType(
        ast.AccessControl.READONLY, base, rank=from_reference(node.dimensions)
    )


def _with_access(type_: Any, access: ref.AccessControl | None) -> Any:
    if isinstance(type_, ast.ArrayReferenceType) and access is not None:
        type_.access = ast.AccessControl(access.name)
    return type_


def from_reference(node: Any) -> Any:
    """Convert a reference AST node (or a list of them) to our AST."""
    if node is None:
        return None
    if isinstance(node, list):
        return [from_reference(item) for item in node]
    out = _FROM[type(node)](node)
    if isinstance(node, ref.Statement) and isinstance(out, ast.Statement):
        out.annotations = from_reference(node.annotations)
    return out


def remove_spans(node: Any) -> Any:
    """A copy of a reference node with every span cleared (for comparisons)."""
    if isinstance(node, list):
        return [remove_spans(item) for item in node]
    if isinstance(node, tuple):
        return tuple(remove_spans(item) for item in node)
    if not isinstance(node, ref.QASMNode):
        return node
    kwargs, no_init = {}, {}
    for field in dataclasses.fields(node):
        if field.name == "span":
            continue
        target = kwargs if field.init else no_init
        target[field.name] = remove_spans(getattr(node, field.name))
    out = type(node)(**kwargs)
    for name, value in no_init.items():
        setattr(out, name, value)
    return out
