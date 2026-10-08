"""Differential tests against the reference parser of the OpenQASM project.

Every program of the corpus is parsed by the reference ANTLR-based parser
(``openqasm3.parse``) and by :func:`openqasm.parse`. Both must accept or both
must reject it, and accepted programs must produce the same tree.

The corpus is read from a checkout of https://github.com/openqasm/openqasm
named by the ``OPENQASM_REPO`` environment variable:

* ``examples/*.qasm``: the example programs of the specification;
* ``source/grammar/tests/reference/**.yaml``: grammar reference programs;
* ``source/grammar/tests/invalid/statements/*.qasm``: one invalid statement per line;
* ``source/language/*.rst``: every code block of the specification text.
"""

from __future__ import annotations

import os
import pathlib
import re
import textwrap

import pytest

yaml = pytest.importorskip("yaml")
pytest.importorskip("antlr4")
openqasm3 = pytest.importorskip("openqasm3")

from openqasm3.parser import QASM3ParsingError  # noqa: E402
from openqasm3.parser import parse as reference_parse  # noqa: E402
from reference_adapter import remove_spans, to_reference  # noqa: E402

import openqasm  # noqa: E402

REPO = os.environ.get("OPENQASM_REPO")
if REPO is None:
    pytest.skip("OPENQASM_REPO is not set", allow_module_level=True)
REPO_DIR = pathlib.Path(REPO)
GRAMMAR_TESTS = REPO_DIR / "source" / "grammar" / "tests"

#: Inputs on which this package intentionally disagrees with the reference.
#: Keys are corpus ids, values explain the difference.
KNOWN_DIFFERENCES: dict[str, str] = {}


def _examples():
    for path in sorted((REPO_DIR / "examples").glob("*.qasm")):
        yield pytest.param(path.read_text(encoding="utf-8"), id=f"examples/{path.name}")


def _grammar_references():
    for path in sorted((GRAMMAR_TESTS / "reference").rglob("*.yaml")):
        source = yaml.safe_load(path.read_text(encoding="utf-8"))["source"]
        yield pytest.param(source, id=f"grammar/{path.relative_to(GRAMMAR_TESTS / 'reference')}")


def _invalid_statements():
    for path in sorted((GRAMMAR_TESTS / "invalid" / "statements").glob("*.qasm")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if line and not line.startswith("//"):
                yield pytest.param(line, id=f"invalid/{path.name}:{number}")


_CODE_BLOCK = re.compile(
    r"^(\s*)\.\. code-block::[ \t]*\n((?:\1[ \t]+:.*\n)*)\n((?:(?:\1[ \t]+.*)?\n)+)", re.M
)


def _spec_code_blocks():
    for path in sorted((REPO_DIR / "source" / "language").glob("*.rst")):
        text = path.read_text(encoding="utf-8")
        for number, match in enumerate(_CODE_BLOCK.finditer(text), 1):
            yield pytest.param(textwrap.dedent(match.group(3)), id=f"spec/{path.name}#{number}")


def _outcome(parse, source):
    try:
        return "ok", parse(source)
    except (openqasm.QasmError, QASM3ParsingError) as exc:
        return "error", exc
    except Exception as exc:  # noqa: BLE001 - anything else is a crash
        return "crash", exc


def _ours(source):
    program = openqasm.parse(source)
    return remove_spans(to_reference(program, source))


def _theirs(source):
    return remove_spans(reference_parse(source))


def _compare(source, case_id):
    if case_id in KNOWN_DIFFERENCES:
        pytest.xfail(KNOWN_DIFFERENCES[case_id])
    reference_status, reference_result = _outcome(_theirs, source)
    our_status, our_result = _outcome(_ours, source)
    assert our_status != "crash", f"openqasm crashed: {our_result!r}"
    if reference_status == "crash":
        # Known reference bugs: empty or comment-only programs, and
        # 'def f(creg c[n])' arguments crash the reference parser. There is
        # nothing to compare with, but the program must still parse.
        assert our_status == "ok", f"reference crashed ({reference_result!r}); ours: {our_result!r}"
        return
    assert our_status == reference_status, (
        f"reference: {reference_status} ({reference_result!r}); ours: {our_status} ({our_result!r})"
    )
    if our_status == "ok":
        assert our_result == reference_result


@pytest.mark.parametrize("source", list(_examples()))
def test_examples(source, request):
    _compare(source, request.node.callspec.id)


@pytest.mark.parametrize("source", list(_grammar_references()))
def test_grammar_references(source, request):
    _compare(source, request.node.callspec.id)


@pytest.mark.parametrize("source", list(_invalid_statements()))
def test_invalid_statements(source, request):
    _compare(source, request.node.callspec.id)
    with pytest.raises(openqasm.QasmSyntaxError):
        openqasm.parse(source)


@pytest.mark.parametrize("source", list(_spec_code_blocks()))
def test_specification_code_blocks(source, request):
    _compare(source, request.node.callspec.id)
