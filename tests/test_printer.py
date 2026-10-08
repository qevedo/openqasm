import textwrap

import pytest

from openqasm import ast, dumps, parse, parse_expression

PROGRAM = """\
OPENQASM 3.1;
include "stdgates.inc";
input angle[32] theta;
const int[32] n = 4;
qubit[n] q;
bit[n] c;
@reversible
gate g(a) x, y {
  ctrl @ rx(a) x, y;
  gphase(-a / 2);
}
def parity(bit[n] b) -> bit {
  bit p = 0;
  for uint i in [0:n - 1] {
    p ^= b[i];
  }
  return p;
}
h q;
inv @ pow(2) @ g(theta) q[0], q[1];
rz(theta)[100.0ns] q[2];
c = measure q;
if (parity(c) == 1) {
  x q[0];
} else if (c[1]) {
  y q[1];
} else {
  z q[2];
}
switch (int(c)) {
  case 0, 1 {
    reset q;
  }
  default {
  }
}
while (c[0] != 0) {
  c[0] = measure q[0];
}
box[1.0us] {
  delay[50.0ns] q;
  barrier q;
}
let pair = q[0:1] ++ q[{2, 3}];
array[float[64], 2, 2] m = {{1.0, 0.0}, {0.0, 1.0}};
duration t = durationof({
  x q[0];
}) + 2.0ns;
"""


def test_canonical_program_round_trips_exactly():
    assert dumps(parse(PROGRAM)) == PROGRAM


def test_printing_is_stable_and_preserves_the_tree():
    source = "if(a){x q;}else if(b){y q;}for int i in{1,2}{a+=i*2**-1;}"
    program = parse(source)
    printed = dumps(program)
    assert parse(printed) == program
    assert dumps(parse(printed)) == printed


@pytest.mark.parametrize(
    "source",
    [
        "(a + b) * c",
        "a - (b - c)",
        "a - b - c",
        "-a ** 2",
        "(-a) ** 2",
        "a ** b ** c",
        "(a ** b) ** c",
        "~(a & b)",
        "(a || b) && c",
        "(a + b)[0]",
        "!(a == b)",
    ],
)
def test_parentheses_follow_precedence(source):
    assert dumps(parse_expression(source)) == source


def test_old_style_declarations_and_measurements_keep_their_spelling():
    source = "qreg q[2];\ncreg c[2];\nmeasure q -> c;\nc = measure q;\n"
    assert dumps(parse(source)) == source


def test_old_measurement_option():
    assert dumps(parse("c = measure q;"), old_measurement=True) == "measure q -> c;\n"


def test_chain_else_if_option():
    source = "if (a) {\n} else if (b) {\n}\n"
    assert dumps(parse(source)) == source
    assert dumps(parse(source), chain_else_if=False) == "if (a) {\n} else {\n  if (b) {\n  }\n}\n"


def test_indent_option():
    assert dumps(parse("def f() { x $0; }"), indent="\t") == "def f() {\n\tx $0;\n}\n"


def test_openqasm2_programs_are_printed_as_openqasm2():
    source = textwrap.dedent(
        """\
        OPENQASM 2.0;
        include "qelib1.inc";
        opaque magic(a) q;
        qreg q[2];
        creg c[2];
        u1(2 ^ 3) q[0];
        measure q -> c;
        if (c == 1) x q[0];
        """
    )
    assert dumps(parse(source)) == source


def test_calibration_bodies_are_verbatim():
    source = "defcal x $0 {\n  play(d0, gaussian(...));\n}\ncal {\n    raw\n}\n"
    assert dumps(parse(source)) == source


def test_print_fragments():
    assert dumps(ast.IntType(ast.IntegerLiteral(8))) == "int[8]"
    assert dumps(ast.MeasureExpression(ast.HardwareQubit(1))) == "measure $1"
    assert dumps(ast.Reset(ast.Identifier("q"))) == "reset q;\n"
    assert dumps(ast.BitstringLiteral(5, 4)) == '"0101"'
    assert dumps(ast.FloatLiteral(1e-7)) == "1e-07"
    assert dumps(parse_expression("1e999")) == "1.0e999"


def test_annotations_and_pragmas():
    source = "pragma a b c\n@one\n@two args here\nx $0;\n"
    assert dumps(parse(source)) == source


def test_unknown_nodes_are_rejected():
    with pytest.raises(TypeError):
        dumps(ast.Statement())
