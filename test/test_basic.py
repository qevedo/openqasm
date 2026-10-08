"""Test for the QASM parser"""

import os
import unittest
import ply

from openqasm import Qasm, QasmError
from openqasm.node.node import Node
from openqasm.test import OpenqasmTestCase, Path

def parse(file_path, prec=15):
    """
      Simple helper
      - file_path: Path to the OpenQASM file
      - prec: Precision for the returned string
    """
    qasm = Qasm(file_path)
    return qasm.parse().qasm(prec)


def parse_data(data, prec=15):
    """Parse an OpenQASM string and return the regenerated source."""
    return Qasm(data=data).parse().qasm(prec)


class TestParser(OpenqasmTestCase):
    """QasmParser"""

    def setUp(self):
        self.qasm_file_path = self._get_resource_path('example.qasm', Path.QASMS)
        self.qasm_file_path_fail = self._get_resource_path('example_fail.qasm', Path.QASMS)
        self.qasm_file_path_if = self._get_resource_path('example_if.qasm', Path.QASMS)

    def test_parser(self):
        """should return a correct response for a valid circuit."""

        res = parse(self.qasm_file_path)
        self.log.info(res)
        starts_expected = "OPENQASM 2.0;\ngate u3(theta,phi,lambda) q\n"
        ends_expected = "\n".join([
            "}",
            "qreg q[3];",
            "qreg r[3];",
            "h q;",
            "cx q,r;",
            "creg c[3];",
            "creg d[3];",
            "barrier q;",
            "measure q -> c;",
            "measure r -> d;",
            "",
        ])
        self.assertEqual(res[:len(starts_expected)], starts_expected)
        self.assertEqual(res[-len(ends_expected):], ends_expected)

    def test_parser_fail(self):
        """should fail a for a  not valid circuit."""

        self.assertRaisesRegex(
            QasmError, "Perhaps there is a missing",
            parse, file_path=self.qasm_file_path_fail
        )

    def test_parser_version_fail(self):
        """Versions other than 2.0 (or 2) are rejected."""
        for filename in ('example_version_fail.qasm', 'example_minor_version_fail.qasm'):
            with self.subTest(filename=filename):
                with self.assertRaisesRegex(
                        QasmError,
                        r"Invalid version: '.+'\. This module supports OpenQASM 2\.0 only\."):
                    parse(self._get_resource_path(filename, Path.QASMS))

    def test_parser_version_2(self):
        """'OPENQASM 2;' is accepted and normalised to 2.0."""
        res = parse(self._get_resource_path('example_version_2.qasm', Path.QASMS))
        self.assertTrue(res.startswith("OPENQASM 2.0;"))

    def test_all_valid_nodes(self):
        """Test that the tree contains only Node subclasses."""
        def inspect(node):
            """Inspect node children."""
            for child in node.children:
                self.assertTrue(isinstance(child, Node))
                inspect(child)

        # Test the canonical example file.
        qasm = Qasm(self.qasm_file_path)
        res = qasm.parse()
        inspect(res)

        # Test a file containing if instructions.
        qasm_if = Qasm(self.qasm_file_path_if)
        res_if = qasm_if.parse()
        inspect(res_if)

    def test_generate_tokens(self):
        """Test whether we get only valid tokens."""
        qasm = Qasm(self.qasm_file_path)
        for token in qasm.generate_tokens():
            self.assertTrue(isinstance(token, ply.lex.LexToken))

    def test_generate_tokens_from_data(self):
        """Tokens are produced when the program is given as a string."""
        tokens = list(Qasm(data='OPENQASM 2.0;\nqreg q[2];').generate_tokens())
        self.assertEqual([tok.type for tok in tokens],
                         ['FORMAT', ';', 'QREG', 'ID', '[', 'NNINTEGER', ']', ';'])

    def test_all_qelib1_gates(self):
        """Every gate of the current qelib1.inc can be used."""
        res = parse(self._get_resource_path('all_gates.qasm', Path.QASMS))
        for gate in ('sx', 'sxdg', 'rxx', 'rzz', 'rccx', 'rc3x', 'c3x', 'c3sqrtx',
                     'c4x', 'csx', 'cu', 'cp', 'crx', 'cry', 'p', 'u'):
            self.assertIn('\ngate %s' % gate, res)

    def test_spec_examples(self):
        """The example circuits of the OpenQASM 2 specification parse."""
        for filename in sorted(os.listdir(Path.SPEC.value)):
            with self.subTest(filename=filename):
                ast = Qasm(os.path.join(Path.SPEC.value, filename)).parse()
                self.assertEqual(ast.qasm(15)[:13], "OPENQASM 2.0;")

    def test_spec_invalid_examples(self):
        """The invalid example circuits of the specification are rejected."""
        for filename in sorted(os.listdir(Path.INVALID.value)):
            with self.subTest(filename=filename):
                with self.assertRaises(QasmError):
                    Qasm(os.path.join(Path.INVALID.value, filename)).parse()

    def test_roundtrip(self):
        """Regenerated source parses to the same program."""
        first = parse(self.qasm_file_path_if)
        self.assertEqual(parse_data(first), first)

    def test_illegal_character(self):
        """Unknown characters raise a QasmError instead of printing."""
        with self.assertRaisesRegex(QasmError, "Unable to match any token rule"):
            parse_data('OPENQASM 2.0;\nqreg q[1];\n$')

    def test_syntax_error_location(self):
        """Syntax errors report where they happened."""
        with self.assertRaisesRegex(QasmError, "line 3"):
            parse_data('OPENQASM 2.0;\nqreg q[1];\nqreg q[1] q;\n')

    def test_real_value(self):
        """Real literals evaluate to floats."""
        ast = Qasm(data='OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[1];\n'
                        'u1(0.5*pi) q[0];\nrz(-2^2) q[0];').parse()
        u1_args = ast.children[-2].arguments.children
        rz_args = ast.children[-1].arguments.children
        self.assertAlmostEqual(u1_args[0].real(), 1.5707963267948966)
        self.assertEqual(rz_args[0].real(), -4.0)

    def test_include_relative_to_file(self):
        """Includes are resolved relative to the including file."""
        res = parse(self._get_resource_path('include_relative.qasm', Path.QASMS))
        self.assertIn("gate mygate a", res)

    def test_include_missing(self):
        """A missing include raises a QasmError."""
        with self.assertRaisesRegex(QasmError, "cannot be found"):
            parse_data('OPENQASM 2.0;\ninclude "does-not-exist.inc";\n')

    def test_redefine_library_extension_gate(self):
        """Gates added to qelib1.inc after the spec can be defined by users."""
        res = parse_data('OPENQASM 2.0;\ninclude "qelib1.inc";\n'
                         'gate cu(a) c,t { cu1(a) c,t; }\nqreg q[2];\ncu(pi) q[0],q[1];\n')
        self.assertEqual(res.count('gate cu('), 1)
        self.assertIn('gate cu(a) c,t', res)
        self.assertEqual(parse_data(res), res)

    def test_redefine_spec_gate_fails(self):
        """Gates of the specification's qelib1.inc cannot be redefined."""
        with self.assertRaisesRegex(QasmError, "Duplicate declaration for gate 'cx'"):
            parse_data('OPENQASM 2.0;\ninclude "qelib1.inc";\ngate cx a,b { CX a,b; }\n')

    def test_redefine_user_gate_fails(self):
        """A program cannot define the same gate twice."""
        with self.assertRaisesRegex(QasmError, "Duplicate declaration for gate 'g'"):
            parse_data('OPENQASM 2.0;\ngate g a { U(0,0,0) a; }\ngate g a { U(0,0,0) a; }\n')


if __name__ == '__main__':
    unittest.main()
