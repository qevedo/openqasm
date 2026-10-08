"""Semantic analysis of the example programs of the specification.

Every example must pass :func:`openqasm.analyze`, except the few that contain
real mistakes; for those, the analyser must report exactly the known problems.
"""

from __future__ import annotations

import os
import pathlib

import pytest

import openqasm

REPO = os.environ.get("OPENQASM_REPO")
if REPO is None:
    pytest.skip("OPENQASM_REPO is not set", allow_module_level=True)
EXAMPLES = sorted((pathlib.Path(REPO) / "examples").glob("*.qasm"))

#: Mistakes in the upstream examples, as (line, message fragment).
KNOWN_INVALID = {
    # 'first_dimension' is declared twice in the global scope.
    "arrays.qasm": [(76, "'first_dimension' is already declared")],
    # A fragment: no 'include "stdgates.inc"' for CX and no declaration of q.
    "cphase.qasm": [
        (4, "undefined gate 'CX'"),
        (6, "undefined gate 'CX'"),
        (9, "undefined identifier 'q'"),
        (9, "undefined identifier 'q'"),
    ],
    # stdgates.inc has no gate 'u' (and it would need three parameters).
    "dd.qasm": [(25, "undefined gate 'u'")],
    # 'scratch' is a qubit[3], and 'success' is never declared.
    "msd.qasm": [
        (48, "index 3 is out of range"),
        (80, "undefined identifier 'success'"),
        (81, "undefined identifier 'success'"),
    ],
    # Gate qubit arguments are single qubits and cannot be indexed.
    "vqe.qasm": [(25, "cannot be indexed"), (25, "cannot be indexed")],
}


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example(path):
    program = openqasm.parse(path.read_text(encoding="utf-8"), filename=str(path))
    analysis = openqasm.analyze(program)
    found = [(error.span.start_line, error.message) for error in analysis.errors]
    expected = KNOWN_INVALID.get(path.name, [])
    assert len(found) == len(expected), found
    for (line, message), (expected_line, fragment) in zip(found, expected, strict=True):
        assert line == expected_line and fragment in message, found
