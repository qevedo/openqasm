import pytest

from openqasm import QasmSyntaxError, UnsupportedVersionError, ast, parse, parse_expression


def statement(source):
    (only,) = parse(source).statements
    return only


def expression(source):
    return parse_expression(source)


ID = ast.Identifier
INT = ast.IntegerLiteral


class TestProgram:
    def test_version(self):
        assert parse("OPENQASM 3.0;").version == "3.0"
        assert parse("OPENQASM 3;").version_tuple == (3,)
        assert parse("qubit q;").version is None

    @pytest.mark.parametrize("version", ["1.0", "3.5", "4"])
    def test_unsupported_versions(self, version):
        with pytest.raises(UnsupportedVersionError, match="unsupported version"):
            parse(f"OPENQASM {version};")
        assert parse(f"OPENQASM {version};", ignore_version=True).version == version

    def test_version_must_come_first(self):
        with pytest.raises(QasmSyntaxError, match="must be the first statement"):
            parse("qubit q;\nOPENQASM 3.0;")

    def test_empty_and_comment_only_programs(self):
        assert parse("").statements == []
        program = parse("// nothing here\n/* at all */")
        assert program.statements == []
        assert len(program.comments) == 2

    def test_spans(self):
        program = parse("qubit q;\n  h q;", filename="x.qasm")
        gate = program.statements[1]
        assert (gate.span.start_line, gate.span.start_column, gate.span.end_column) == (2, 2, 6)
        assert gate.span.file == "x.qasm"
        assert str(gate.span) == "x.qasm:2:3"


class TestDeclarations:
    def test_qubits(self):
        assert statement("qubit q;") == ast.QubitDeclaration(ID("q"))
        assert statement("qubit[4] q;") == ast.QubitDeclaration(ID("q"), INT(4))
        legacy = statement("qreg q[4];")
        assert legacy == ast.QubitDeclaration(ID("q"), INT(4))
        assert legacy.legacy_syntax

    def test_classical(self):
        assert statement("int[8] a = 1;") == ast.ClassicalDeclaration(
            ast.IntType(INT(8)), ID("a"), INT(1)
        )
        assert statement("creg c[2];") == ast.ClassicalDeclaration(ast.BitType(INT(2)), ID("c"))
        assert statement("complex[float[64]] z;").type == ast.ComplexType(ast.FloatType(INT(64)))
        assert statement("duration d = 10ns;").init == ast.DurationLiteral(10.0, ast.TimeUnit.NS)

    def test_arrays(self):
        decl = statement("array[int[8], 2, 2] a = {{1, 2}, {3, 4}};")
        assert decl.type == ast.ArrayType(ast.IntType(INT(8)), [INT(2), INT(2)])
        assert decl.init == ast.ArrayLiteral(
            [ast.ArrayLiteral([INT(1), INT(2)]), ast.ArrayLiteral([INT(3), INT(4)])]
        )

    def test_const_io_alias(self):
        assert statement("const float x = 1.5;") == ast.ConstDeclaration(
            ast.FloatType(), ID("x"), ast.FloatLiteral(1.5)
        )
        assert statement("input angle[16] theta;").io is ast.IOKeyword.INPUT
        alias = statement("let a = q[0:1] ++ r;")
        assert isinstance(alias.value, ast.Concatenation)

    def test_measure_initialiser(self):
        assert statement("bit b = measure $0;").init == ast.MeasureExpression(ast.HardwareQubit(0))

    @pytest.mark.parametrize(
        ("source", "message"),
        [
            ("int[0] a;", "int size must be positive"),
            ("uint[-1] a;", "uint size must be positive"),
            ("qreg q[0];", "qreg size must be positive"),
            ("complex[int] z;", "invalid type of complex components"),
            ("array[stretch, 2] a;", "invalid scalar type for array"),
            ("array[int, -1] a;", "array dimensions must be non-negative"),
            ("int a b;", "expected ';'"),
            ("float;", "expected an identifier"),
        ],
    )
    def test_invalid(self, source, message):
        with pytest.raises(QasmSyntaxError, match=message):
            parse(source)


class TestExpressions:
    def test_precedence(self):
        assert expression("1 + 2 * 3") == ast.BinaryExpression(
            ast.BinaryOperator.ADD,
            INT(1),
            ast.BinaryExpression(ast.BinaryOperator.MULTIPLY, INT(2), INT(3)),
        )
        assert expression("a || b && c").op is ast.BinaryOperator.LOGICAL_OR
        assert expression("a == b < c").op is ast.BinaryOperator.EQUAL
        assert expression("a & b ^ c | d").op is ast.BinaryOperator.BIT_OR

    def test_power_is_right_associative_and_binds_tighter_than_negation(self):
        power = expression("2 ** 3 ** 4")
        assert power.lhs == INT(2) and isinstance(power.rhs, ast.BinaryExpression)
        negation = expression("-2 ** 2")
        assert isinstance(negation, ast.UnaryExpression)
        assert negation.operand.op is ast.BinaryOperator.POWER
        assert expression("2 ** -1").rhs == ast.UnaryExpression(ast.UnaryOperator.NEGATE, INT(1))

    def test_left_associativity(self):
        assert expression("a - b - c").lhs == ast.BinaryExpression(
            ast.BinaryOperator.SUBTRACT, ID("a"), ID("b")
        )

    def test_postfix_index(self):
        assert expression("-a[0]") == ast.UnaryExpression(
            ast.UnaryOperator.NEGATE, ast.IndexExpression(ID("a"), [INT(0)])
        )
        assert expression("a[1:2][0]").collection == ast.IndexExpression(
            ID("a"), [ast.Range(INT(1), INT(2))]
        )
        assert expression("a[{0, 2}]").index == ast.DiscreteSet([INT(0), INT(2)])
        assert expression("a[0:2:10]").index == [ast.Range(INT(0), INT(10), INT(2))]
        assert expression("a[:]").index == [ast.Range()]
        assert expression("a[1, 2]").index == [INT(1), INT(2)]

    def test_literals(self):
        assert expression('"01_10"') == ast.BitstringLiteral(0b0110, 4)
        assert expression("true") == ast.BooleanLiteral(True)
        assert expression("1.5im") == ast.ImaginaryLiteral(1.5)
        assert expression("$3") == ast.HardwareQubit(3)

    def test_calls_casts_and_durationof(self):
        assert expression("sin(pi / 2)") == ast.FunctionCall(
            ID("sin"), [ast.BinaryExpression(ast.BinaryOperator.DIVIDE, ID("pi"), INT(2))]
        )
        assert expression("int[8](x)") == ast.Cast(ast.IntType(INT(8)), ID("x"))
        assert isinstance(expression("durationof({ x $0; })"), ast.DurationOf)

    @pytest.mark.parametrize(
        ("source", "message"),
        [
            ('"012"', "invalid bit-string literal"),
            ("'01'", "invalid bit-string literal"),
            ("sizeof()", "'sizeof' needs either one or two arguments"),
            ("(1 + 2", "expected '\\)'"),
            ("1 +", "expected an expression"),
        ],
    )
    def test_invalid(self, source, message):
        with pytest.raises(QasmSyntaxError, match=message):
            parse_expression(source)


class TestQuantum:
    def test_gate_call(self):
        call = statement("rx(pi / 2)[100ns] q[0], $1;")
        assert call == ast.GateCall(
            [],
            ID("rx"),
            [ast.BinaryExpression(ast.BinaryOperator.DIVIDE, ID("pi"), INT(2))],
            [ast.IndexedIdentifier(ID("q"), [[INT(0)]]), ast.HardwareQubit(1)],
            ast.DurationLiteral(100.0, ast.TimeUnit.NS),
        )

    def test_modifiers_and_gphase(self):
        call = statement("inv @ pow(2) @ negctrl(2) @ ctrl @ x a, b, c, d;")
        assert [m.kind.value for m in call.modifiers] == ["inv", "pow", "negctrl", "ctrl"]
        assert call.modifiers[2].argument == INT(2)
        assert statement("gphase(pi);") == ast.GlobalPhase([], ID("pi"))
        assert statement("ctrl @ gphase(pi) q;").qubits == [ID("q")]
        with pytest.raises(QasmSyntaxError, match="'gphase' takes exactly one argument"):
            parse("gphase(1, 2);")

    def test_gate_definition(self):
        gate = statement("gate g(a, b) q, r { U(a, b, 0) q; }")
        assert [p.name for p in gate.parameters] == ["a", "b"]
        assert [q.name for q in gate.qubits] == ["q", "r"]
        assert isinstance(gate.body[0], ast.GateCall)

    def test_measurements(self):
        arrow = statement("measure q -> c;")
        assert arrow == ast.MeasureStatement(ast.MeasureExpression(ID("q")), ID("c"))
        assert arrow.arrow_syntax
        assignment = statement("c[0] = measure q[0];")
        assert assignment.target == ast.IndexedIdentifier(ID("c"), [[INT(0)]])
        assert not assignment.arrow_syntax
        assert statement("measure $0;").target is None

    def test_measurement_through_a_defcal(self):
        call = statement("measure_iq(1.0) $0 -> c;").measure
        assert call == ast.QuantumCall(
            ID("measure_iq"), [ast.FloatLiteral(1.0)], [ast.HardwareQubit(0)]
        )
        assert isinstance(statement("c = measure_iq $0;").measure, ast.QuantumCall)

    def test_other_instructions(self):
        assert statement("reset q;") == ast.Reset(ID("q"))
        assert statement("barrier;") == ast.Barrier([])
        assert statement("barrier q, r[0];").operands[1] == ast.IndexedIdentifier(
            ID("r"), [[INT(0)]]
        )
        assert statement("delay[d] q;") == ast.Delay(ID("d"), [ID("q")])
        assert statement("nop $0;") == ast.Nop([ast.HardwareQubit(0)])
        assert statement("box[1us] { x q; }").duration == ast.DurationLiteral(1.0, ast.TimeUnit.US)

    def test_calibration(self):
        assert statement('defcalgrammar "openpulse";') == ast.CalibrationGrammar("openpulse")
        assert statement("cal { anything { } }") == ast.CalibrationBlock(" anything { } ")
        defcal = statement("defcal rx(angle[20] a) $0 -> bit { body }")
        assert defcal.arguments == [ast.ClassicalArgument(ast.AngleType(INT(20)), ID("a"))]
        assert defcal.operands == [ast.HardwareQubit(0)]
        assert defcal.return_type == ast.BitType()
        assert statement("defcal rx(pi) q { }").arguments == [ID("pi")]


class TestClassical:
    def test_assignments(self):
        assert statement("a = 1;") == ast.Assignment(ID("a"), ast.AssignmentOperator.ASSIGN, INT(1))
        assert statement("a[0][1] **= 2;").target == ast.IndexedIdentifier(
            ID("a"), [[INT(0)], [INT(1)]]
        )
        with pytest.raises(QasmSyntaxError, match="invalid assignment target"):
            parse("f(x) = 1;")

    def test_expression_statements(self):
        assert statement("f(1);") == ast.ExpressionStatement(ast.FunctionCall(ID("f"), [INT(1)]))
        assert statement("int(x);") == ast.ExpressionStatement(ast.Cast(ast.IntType(), ID("x")))
        assert statement("a b;") == ast.GateCall([], ID("a"), [], [ID("b")])

    def test_control_flow(self):
        branch = statement("if (a) x q; else { y q; z q; }")
        assert len(branch.then_body) == 1 and len(branch.else_body) == 2
        loop = statement("for uint i in [0:4] { }")
        assert loop.iterable == ast.Range(INT(0), INT(4))
        assert statement("for int i in {1, 2} { }").iterable == ast.DiscreteSet([INT(1), INT(2)])
        assert statement("for bit b in reg { }").iterable == ID("reg")
        assert isinstance(statement("while (true) { break; continue; }").body[0], ast.Break)

    def test_switch(self):
        switch = statement("switch (i) { case 0, 1 { } default { x q; } }")
        assert switch.cases[0].values == [INT(0), INT(1)]
        assert len(switch.default) == 1
        with pytest.raises(QasmSyntaxError, match="'case' statement after 'default'"):
            parse("switch (i) { default { } case 1 { } }")
        with pytest.raises(QasmSyntaxError, match="multiple 'default' cases"):
            parse("switch (i) { default { } default { } }")

    def test_subroutines_and_externs(self):
        sub = statement(
            "def f(int[8] a, qubit[2] q, readonly array[int, #dim = 2] b, creg c[2]) -> bit { return measure q[0]; }"
        )
        assert sub.arguments[1] == ast.QuantumArgument(ID("q"), INT(2))
        assert sub.arguments[2].type.rank == INT(2)
        assert sub.arguments[3].legacy_syntax
        assert sub.return_type == ast.BitType()
        extern = statement("extern g(mutable array[float, 3], creg[4]) -> int;")
        assert extern.arguments[0].type.access is ast.AccessControl.MUTABLE

    def test_annotations_and_pragmas(self):
        program = parse("pragma hello world\n@a.b c d\n@e\nx q;")
        assert program.statements[0] == ast.Pragma("hello world")
        assert program.statements[1].annotations == [
            ast.Annotation("a.b", "c d"),
            ast.Annotation("e"),
        ]


class TestPlacementRules:
    @pytest.mark.parametrize(
        ("source", "message"),
        [
            ("break;", "'break' statement outside loop"),
            ("while (true) { def f() { continue; } }", "subroutine definitions must be global"),
            ("def f() { break; }", "'break' statement outside loop"),
            ("return;", "'return' statement outside subroutine"),
            ("if (true) { qubit q; }", "qubit declarations must be global"),
            ("{ qubit q; }", "qubit declarations must be global"),
            ("def f() { gate g q { } }", "gate definitions must be global"),
            ("if (true) { pragma x\n}", "pragmas must be global"),
            ("if (true) { array[int, 2] a; }", "arrays can only be declared globally"),
            ("gate g q { int a; }", "cannot declare classical variables in a gate"),
            ("gate g q { measure q; }", "non-unitary 'measure'"),
            ("gate g q { reset q; }", "non-unitary 'reset'"),
            ("int a; gate g q { a = 1; }", "cannot assign to classical parameters in a gate"),
            ('if (true) { include "x.inc"; }', "'include' statements must be global"),
        ],
    )
    def test_rules(self, source, message):
        with pytest.raises(QasmSyntaxError, match=message):
            parse(source)

    def test_switch_cases_are_local_scopes(self):
        with pytest.raises(QasmSyntaxError, match="qubit declarations must be global"):
            parse("switch (i) { case 0 { qubit q; } }")


class TestOpenQASM2:
    def test_opaque_and_power(self):
        program = parse("OPENQASM 2.0;\nopaque g(a) q, r;\nqreg q[1];\nU(2^3^2, 0, 0) q[0];")
        assert program.statements[0] == ast.OpaqueDefinition(ID("g"), [ID("a")], [ID("q"), ID("r")])
        power = program.statements[2].arguments[0]
        assert power.op is ast.BinaryOperator.POWER
        assert power.rhs.op is ast.BinaryOperator.POWER

    def test_caret_is_xor_in_openqasm3(self):
        assert (
            parse("OPENQASM 3.0;\na ^ b;").statements[0].expression.op is ast.BinaryOperator.BIT_XOR
        )

    def test_opaque_is_an_identifier_in_openqasm3(self):
        assert isinstance(parse("opaque q;").statements[0], ast.GateCall)


def test_syntax_error_location():
    with pytest.raises(QasmSyntaxError) as info:
        parse("qubit q;\nh q\nx q;", filename="prog.qasm")
    error = info.value
    assert (error.line, error.column) == (3, 0)
    assert str(error) == "prog.qasm:3:1: expected ';', found identifier 'x'"
    assert error.render("qubit q;\nh q\nx q;") == (
        "prog.qasm:3:1: error: expected ';', found identifier 'x'\n  x q;\n  ^"
    )
