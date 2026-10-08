"""Pytest plugin running the reference ``openqasm3`` test suite against this package.

Load it with ``pytest -p upstream_plugin <openqasm checkout>/source/openqasm/tests``
(``tests/conformance`` must be on ``PYTHONPATH``). It replaces the reference
parser and printer with adapters around :func:`openqasm.parse` and
:func:`openqasm.dumps`; the tests themselves run unmodified.

Tests that check behaviour where this package deliberately differs from the
reference are marked as expected failures in :data:`EXPECTED_DIFFERENCES`.
"""

from __future__ import annotations

import sys
import types

import openqasm3
import pytest
from reference_adapter import ReferenceSpans, from_reference, to_reference

import openqasm

#: Upstream tests whose expectations this package intentionally does not meet.
EXPECTED_DIFFERENCES = {
    # OpenQASM 2 programs are accepted (they are parsed in compatibility mode).
    "test_rejects_invalid_version[2.0]": "openqasm accepts OpenQASM 2.0 programs",
    # The test matches the wording of an ANTLR lexer error; '$a' is rejected
    # with a different message (see tests/test_lexer.py).
    "test_non_integer_physical_qubit_raises": "error message wording differs from ANTLR",
}

#: Upstream test files that test reference-only helpers and never reach our code.
NOT_APPLICABLE = {
    "test_version.py",  # openqasm3.parse_version()
    "test_spec.py",  # openqasm3.spec.supported_versions
}


class QASM3ParsingError(Exception):
    """Stand-in for ``openqasm3.parser.QASM3ParsingError``."""


def parse(input_: str, *, permissive: bool = False, ignore_version: bool = False):
    try:
        program = openqasm.parse(input_, ignore_version=ignore_version)
    except openqasm.QasmSyntaxError as exc:
        raise QASM3ParsingError(str(exc)) from exc
    return to_reference(program, input_)


def dumps(node, **kwargs):
    return openqasm.dumps(from_reference(node), **kwargs)


def dump(node, file, **kwargs):
    file.write(dumps(node, **kwargs))


def get_comments(input_: str) -> list[dict]:
    program = openqasm.parse(input_, ignore_version=True)
    return [
        {
            "type": "block" if comment.is_block else "line",
            "text": comment.text,
            "line": comment.span.start_line,
            "column": comment.span.start_column,
        }
        for comment in program.comments
    ]


def combine_span(first, second):
    return openqasm3.ast.Span(
        first.start_line, first.start_column, second.end_line, second.end_column
    )


_parser_module = types.ModuleType("openqasm3.parser")
_parser_module.parse = parse
_parser_module.QASM3ParsingError = QASM3ParsingError
_parser_module.get_comments = get_comments
_parser_module.combine_span = combine_span
_parser_module.ReferenceSpans = ReferenceSpans
sys.modules["openqasm3.parser"] = _parser_module
openqasm3.parser = _parser_module
openqasm3.parse = parse
openqasm3.dumps = dumps
openqasm3.dump = dump


def pytest_collection_modifyitems(config, items):
    selected, deselected = [], []
    for item in items:
        if item.nodeid.split("::")[0].rsplit("/", 1)[-1] in NOT_APPLICABLE:
            deselected.append(item)
            continue
        selected.append(item)
        reason = EXPECTED_DIFFERENCES.get(item.name)
        if reason is not None:
            item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected
