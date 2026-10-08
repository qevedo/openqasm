import textwrap

import pytest

import openqasm
from openqasm import QasmIncludeError, QasmSemanticError, analyze, parse
from openqasm.semantic import SymbolKind


def errors(source, **options):
    analysis = analyze(parse(textwrap.dedent(source)), **options)
    return [error.message for error in analysis.errors]


def assert_valid(source, **options):
    assert errors(source, **options) == []


def assert_error(source, fragment, **options):
    found = errors(source, **options)
    assert any(fragment in message for message in found), found


VALID_PROGRAMS = {
    "teleport": """
        OPENQASM 3.0;
        include "stdgates.inc";
        qubit[3] q;
        bit[2] c;
        h q[1];
        cx q[1], q[2];
        cx q[0], q[1];
        h q[0];
        c[0] = measure q[0];
        c[1] = measure q[1];
        if (c[1]) x q[2];
        if (c[0] == 1) { z q[2]; }
    """,
    "subroutines": """
        include "stdgates.inc";
        const int[32] n = 3;
        def ghz(qubit[n] q) -> bit[n] {
          h q[0];
          for int i in [0:n - 2] { cx q[i], q[i + 1]; }
          return measure q;
        }
        qubit[n] reg;
        bit[n] out = ghz(reg);
        int ones = popcount(out);
    """,
    "constants and builtins": """
        const float twopi = 2 * pi;
        const uint w = 4;
        const int[w * 2] k = 1 << 3;
        const angle half = π / 2;
        bit[w] b = "1010";
        float r = sqrt(2) * cos(τ) + log(euler) + mod(5, 3);
        complex[float[64]] z = 1.0 + 2.0im;
        float re = real(z);
        array[int[8], w, 2] a;
        const uint s = sizeof(a, 1);
        bit[s] two;
    """,
    "gates and modifiers": """
        include "stdgates.inc";
        gate my(θ) a, b { ctrl @ rz(θ) a, b; gphase(θ); }
        qubit[4] q;
        my(0.5) q[0], q[1];
        inv @ my(0.5) q[2], q[3];
        ctrl(2) @ x q[0], q[1], q[2];
        negctrl @ ctrl @ x q[0], q[1], q[2];
        ctrl @ gphase(pi) q[3];
        h q;
        cx q[0:1], q[2:3];
        barrier q[0], q[0];
        U(0, 0, 0) $5;
    """,
    "timing": """
        include "stdgates.inc";
        stretch g;
        duration d = 100ns + durationof({ x $0; });
        qubit q;
        delay[2 * g] q;
        box[1us] { delay[d] q; x q; }
        duration ratio_check = d / 2;
        float ratio = d / 10ns;
    """,
    "scopes": """
        int ii = 100;
        qubit[5] q;
        let some_q = q[0:2];
        { ii *= 2; int ii = 1; ii *= 2; }
        for uint ii in [1:4] { float ii2 = ii; }
        while (ii > 0) { let some_q = q[3:4]; ii -= 1; }
        const int c = 3;
        def f(uint a, uint c) -> uint { return a + c; }
        def g() -> int { return c; }
    """,
    "switch": """
        int i = 2;
        switch (i) { case 0, 1 { i = 3; } case 2 { } default { } }
    """,
    "io and casts": """
        input float[64] theta;
        output bit[2] result;
        int[8] small = int[8](theta);
        bool flag = bool(result);
        uint[2] u = uint[2](result);
        result = "01";
    """,
    "calibrations": """
        defcalgrammar "openpulse";
        defcal rx(angle[20] theta) $0 { }
        defcal measure_iq $0 -> bit { }
        rx(pi) $0;
        bit b = measure_iq $0;
    """,
    "subroutine called like a gate (earlier drafts)": """
        include "stdgates.inc";
        def bellprep(qubit[2] q) { h q[0]; cx q[0], q[1]; }
        qubit[2] bp;
        bellprep bp;
    """,
}


@pytest.mark.parametrize("source", VALID_PROGRAMS.values(), ids=VALID_PROGRAMS.keys())
def test_valid_programs(source):
    assert_valid(source)


@pytest.mark.parametrize(
    ("source", "message"),
    [
        # names and scopes
        ("x = 1;", "undefined identifier 'x'"),
        ("qubit q; h q;", "undefined gate 'h'"),
        ("int a; int a;", "'a' is already declared in this scope"),
        ("int pi = 3;", "cannot redeclare the built-in 'pi'"),
        ("int a = 1; def f() -> int { return a; }", "'a' is not visible inside a subroutine"),
        ("qubit q; gate g a { U(0, 0, 0) q; }", "'q' is not visible inside a gate"),
        ("int b = 1; gate g(t) a { U(b, 0, 0) a; }", "'b' is not visible inside a gate"),
        ("{ int inner; } inner = 1;", "undefined identifier 'inner'"),
        ("if (true) { int a; } else { a = 1; }", "undefined identifier 'a'"),
        # gates
        ('include "stdgates.inc"; qubit q; rx q;', "gate 'rx' takes 1 parameter, but 0 were given"),
        (
            'include "stdgates.inc"; qubit[2] q; cx q[0];',
            "gate 'cx' acts on 2 qubits, but 1 was given",
        ),
        ('include "stdgates.inc"; qubit[3] q; ctrl(2) @ x q[0], q[1];', "acts on 3 qubits"),
        (
            'include "stdgates.inc"; qubit[2] q; cx q[0], q[0];',
            "the same qubit is used more than once",
        ),
        (
            'include "stdgates.inc"; qubit[2] a; qubit[3] b; cx a, b;',
            "registers of different sizes",
        ),
        ("gate g q { g q; }", "gate 'g' cannot call itself"),
        ('include "stdgates.inc"; gate g q { h q[0]; }', "qubit arguments cannot be indexed"),
        (
            'include "stdgates.inc"; gate g q { h $0; }',
            "hardware qubits cannot be used inside a gate",
        ),
        ("int a; qubit q; a q;", "'a' is a variable, not a gate"),
        ('include "stdgates.inc"; float f = h;', "'h' is a gate, not a value"),
        ('include "stdgates.inc"; int i; h i;', "expected a qubit, found int"),
        ('include "stdgates.inc"; qubit q; ctrl(i) @ x q, q;', "undefined identifier 'i'"),
        (
            'include "stdgates.inc"; int n = 1; qubit[2] q; ctrl(n) @ x q[0], q[1];',
            "must be a constant expression",
        ),
        ("gphase(1) $0;", "gphase acts on 0 control qubits, but 1 was given"),
        # declarations and constants
        (
            "int n = 2; qubit[n] q;",
            "the size of a qubit register must be a constant expression: 'n' is not a constant",
        ),
        ("qubit[0] q;", "must be positive, not 0"),
        ("int x = 1; const int y = x;", "must be a constant expression: 'x' is not a constant"),
        ("const int c = 1; c = 2;", "cannot assign to the constant 'c'"),
        ("input int i; i = 2;", "cannot assign to the input 'i'"),
        ("qubit q; q = 1;", "cannot assign to the qubit 'q'"),
        ("array[int, 2] a = {1, 2, 3};", "expected 2 values in array literal, found 3"),
        ("bit[2] b = 1.5;", "cannot initialise bit[2] 'b' with a value of type float"),
        ("bit[2] b; bit[3] c = b;", "cannot initialise bit[3] 'c' with a value of type bit[2]"),
        ("duration d = 1;", "cannot initialise duration 'd'"),
        ("float f = 1.0im;", "cannot initialise float 'f'"),
        ("let a = 5;", "'let' can only alias"),
        # expressions and types
        ("qubit q; int a = q + 1;", "qubits cannot be used in expressions"),
        ("array[int, 2] a; int b = a + 1;", "arrays cannot be used in expressions"),
        ("bit[4] b; bit c = b[4];", "index 4 is out of range for a register of size 4"),
        ("qubit[2] q; reset q[-3];", "index -3 is out of range"),
        ("qubit q; reset q[0];", "a single qubit cannot be indexed"),
        ("float f = 1.0; int i = f << 2;", "'<<' is not defined for float"),
        ("duration d = 10ns + 1;", "cannot add duration and int"),
        ("float f = sin(1, 2);", "'sin' takes 1 argument, but 2 were given"),
        ("def f(int a) { } f(1, 2);", "'f' takes 1 argument, but 2 were given"),
        ("def f(qubit q) { } int i; f(i);", "argument of 'f' must be a qubit"),
        ("float f = angle(1.0); angle a; int i = int(a);", "angle cannot be cast to int"),
        ("duration d; if (d) { }", "a condition must be a boolean value"),
        ("qubit q; while (q) { }", "qubits cannot be used in conditions"),
        ("float f = 1.0; switch (f) { case 1 { } }", "switch needs an integer value"),
        ("int i; switch (i) { case 1, 1 { } }", "duplicate case value 1"),
        ("int i; int j; switch (i) { case j { } }", "case values must be constant expressions"),
        ("for int i in 5 { }", "cannot loop over int"),
        # measurements
        ("qubit[2] q; bit[3] c = measure q;", "cannot store the measurement of 2 qubits in bit[3]"),
        ("qubit q; float f; f = measure q;", "a measurement result cannot be stored in float"),
        (
            "qubit q; const bit b = measure q;",
            "a constant cannot be initialised with a measurement",
        ),
        # subroutines
        ("def f() -> int { return; }", "this subroutine must return a value of type int"),
        ("def f() { return 1; }", "this subroutine does not return a value"),
        (
            "def f(readonly array[int, 2] a) { a[0] = 1; }",
            "cannot assign to the read-only array 'a'",
        ),
    ],
)
def test_errors(source, message):
    assert_error(source, message)


def test_every_error_is_reported_with_a_location():
    analysis = analyze(parse("x = 1;\ny = 2;", filename="prog.qasm"))
    assert [str(e) for e in analysis.errors] == [
        "prog.qasm:1:1: undefined identifier 'x'",
        "prog.qasm:2:1: undefined identifier 'y'",
    ]


def test_raise_for_errors_collects_all_errors():
    with pytest.raises(QasmSemanticError) as info:
        openqasm.loads("a = 1; b = 2;")
    assert len(info.value.errors) == 2


def test_analysis_summary():
    analysis = analyze(parse('include "stdgates.inc"; qubit[3] q; qubit r; defcal special $0 { }'))
    assert analysis.ok
    assert analysis.includes == ["stdgates.inc"]
    assert analysis.qubits == {"q": 3, "r": 1}
    assert analysis.gates["cx"].qubits == 2
    assert analysis.gates["U"].parameters == 3
    assert "special" in analysis.calibrated_gates
    assert analysis.globals.symbols["q"].kind is SymbolKind.QUBIT


def test_constant_values_are_computed():
    analysis = analyze(
        parse(
            'const int a = 2 ** 5 - 1; const float b = a / 2.0; const bit[4] c = rotl("0011", 1);'
        )
    )
    symbols = analysis.globals.symbols
    assert symbols["a"].value == 31
    assert symbols["b"].value == 15.5
    assert symbols["c"].value.value == 0b0110


class TestIncludes:
    def test_relative_to_the_including_file(self, tmp_path):
        (tmp_path / "lib").mkdir()
        (tmp_path / "lib" / "mine.inc").write_text(
            "gate mine a { U(0, 0, 0) a; }\nconst int size = 2;\n"
        )
        main = tmp_path / "main.qasm"
        main.write_text('include "lib/mine.inc";\nqubit[size] q;\nmine q;\n')
        program = openqasm.load(main)
        assert len(program.statements) == 3

    def test_include_paths(self, tmp_path):
        (tmp_path / "defs.inc").write_text("const int k = 1;\n")
        analysis = analyze(parse('include "defs.inc"; int x = k;'), include_paths=[tmp_path])
        assert analysis.ok
        assert analysis.includes == [str(tmp_path / "defs.inc")]

    def test_missing_include(self):
        analysis = analyze(parse('include "missing.inc";'))
        assert isinstance(analysis.errors[0], QasmIncludeError)

    def test_include_cycle(self, tmp_path):
        (tmp_path / "a.inc").write_text('include "b.inc";\n')
        (tmp_path / "b.inc").write_text('include "a.inc";\n')
        analysis = analyze(parse('include "a.inc";', filename=str(tmp_path / "main.qasm")))
        assert any("includes itself" in e.message for e in analysis.errors)

    def test_errors_in_included_files_point_at_them(self, tmp_path):
        (tmp_path / "bad.inc").write_text("undefined_thing q;\n")
        analysis = analyze(parse('include "bad.inc";', filename=str(tmp_path / "main.qasm")))
        assert analysis.errors[0].span.file.endswith("bad.inc")

    def test_libraries_are_included_once(self):
        assert_valid('include "stdgates.inc"; include "stdgates.inc";')


class TestOpenQASM2:
    def test_qelib1_program(self):
        assert_valid(
            """
            OPENQASM 2.0;
            include "qelib1.inc";
            qreg q[3];
            creg c[3];
            opaque magic(a) x;
            gate majority a,b,c { cx c,b; cx c,a; ccx a,b,c; }
            u3(pi/2, 0, pi^2) q[0];
            sx q[1];
            CX q[0], q[1];
            majority q[0], q[1], q[2];
            magic(ln(2)) q[2];
            measure q -> c;
            if (c == 5) x q[0];
            """
        )

    def test_extension_gates_can_be_redefined(self):
        assert_valid(
            'OPENQASM 2.0; include "qelib1.inc"; gate cu(a) c,t { cu1(a) c,t; } qreg q[2]; cu(pi) q[0],q[1];'
        )

    def test_specification_gates_cannot_be_redefined(self):
        assert_error(
            'OPENQASM 2.0; include "qelib1.inc"; gate cx a,b { CX a,b; }',
            "'cx' is already declared",
        )

    def test_openqasm3_functions_are_not_available(self):
        assert_error(
            "OPENQASM 2.0; qreg q[1]; U(arcsin(1), 0, 0) q[0];", "undefined identifier 'arcsin'"
        )
