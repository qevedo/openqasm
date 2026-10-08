import io
import subprocess
import sys

import pytest

import openqasm
from openqasm import ast
from openqasm.cli import main
from openqasm.visitor import Transformer, Visitor, iter_children, walk


def test_public_api():
    assert openqasm.__version__
    assert openqasm.SUPPORTED_VERSIONS == ("2.0", "3.0", "3.1")
    program = openqasm.loads('include "stdgates.inc"; qubit q; h q;')
    assert isinstance(program, ast.Program)
    stream = io.StringIO()
    openqasm.dump(program, stream)
    assert stream.getvalue() == openqasm.dumps(program)


def test_load(tmp_path):
    path = tmp_path / "bell.qasm"
    path.write_text('include "stdgates.inc";\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\n')
    assert len(openqasm.load(path).statements) == 4
    path.write_text("h q;\n")
    with pytest.raises(openqasm.QasmSemanticError) as info:
        openqasm.load(path)
    assert info.value.span.file == str(path)


def test_nodes_compare_by_content_not_location():
    assert openqasm.parse("qubit  q ;") == openqasm.parse("\n\nqubit q;")
    assert openqasm.parse("qreg q[1];") == openqasm.parse("qubit[1] q;")


def test_walk_and_visitor():
    program = openqasm.parse("int a = 1 + 2; a = a * 3;")
    names = [node.name for node in walk(program) if isinstance(node, ast.Identifier)]
    assert names == ["a", "a", "a"]
    assert len(list(iter_children(program))) == 2

    class CountLiterals(Visitor):
        def __init__(self):
            self.count = 0

        def visit_IntegerLiteral(self, node):
            self.count += 1

    counter = CountLiterals()
    counter.visit(program)
    assert counter.count == 3


def test_transformer_rewrites_and_deletes():
    class Fold(Transformer):
        def visit_BinaryExpression(self, node):
            node = self.generic_visit(node)
            if (
                isinstance(node.lhs, ast.IntegerLiteral)
                and isinstance(node.rhs, ast.IntegerLiteral)
                and node.op is ast.BinaryOperator.ADD
            ):
                return ast.IntegerLiteral(node.lhs.value + node.rhs.value)
            return node

        def visit_Reset(self, node):
            return None

    program = Fold().visit(openqasm.parse("int a = 1 + 2 + 3; reset $0;"))
    assert openqasm.dumps(program) == "int a = 6;\n"


def test_cli_check(tmp_path, capsys):
    good = tmp_path / "good.qasm"
    good.write_text('include "stdgates.inc";\nqubit q;\nh q;\n')
    bad = tmp_path / "bad.qasm"
    bad.write_text("qubit q;\nh q;\n")
    assert main(["check", str(good)]) == 0
    assert capsys.readouterr().out == f"{good}: ok\n"
    assert main(["check", "-q", str(good), str(bad)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{bad}:2:1: error: undefined gate 'h'\n  h q;\n  ^\n"


def test_cli_check_syntax_error_and_missing_file(tmp_path, capsys):
    bad = tmp_path / "bad.qasm"
    bad.write_text("qubit q\n")
    assert main(["check", str(bad)]) == 1
    assert "expected ';'" in capsys.readouterr().err
    assert main(["check", str(tmp_path / "nope.qasm")]) == 2


def test_cli_format(tmp_path, capsys):
    path = tmp_path / "p.qasm"
    path.write_text("// comment\nif(a){x q;}\n")
    assert main(["format", "--indent", "4", str(path)]) == 0
    captured = capsys.readouterr()
    assert captured.out == "if (a) {\n    x q;\n}\n"
    assert "comments are not preserved" in captured.err


def test_module_entry_point():
    result = subprocess.run(
        [sys.executable, "-m", "openqasm", "check", "-"],
        input="qubit q; U(0, 0, 0) q;",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "-: ok\n"
