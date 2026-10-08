import pytest

from openqasm import QasmSyntaxError
from openqasm.lexer import tokenize


def kinds(source):
    tokens, _ = tokenize(source)
    return [token.kind for token in tokens[:-1]]


def values(source):
    tokens, _ = tokenize(source)
    return [token.value for token in tokens[:-1]]


@pytest.mark.parametrize(
    ("source", "value"),
    [
        ("0", 0),
        ("1_000", 1000),
        ("0b1010", 10),
        ("0B1_0", 2),
        ("0o17", 15),
        ("0xFF", 255),
        ("0X1_f", 31),
    ],
)
def test_integer_literals(source, value):
    assert kinds(source) == ["INTEGER"]
    assert values(source) == [value]


@pytest.mark.parametrize(
    ("source", "value"),
    [("1.5", 1.5), ("1.", 1.0), (".5", 0.5), ("1e3", 1000.0), ("2.5E-1", 0.25), ("1_0.0_1", 10.01)],
)
def test_float_literals(source, value):
    assert kinds(source) == ["FLOAT"]
    assert values(source) == [value]


@pytest.mark.parametrize(
    ("source", "value"),
    [
        ("100ns", (100.0, "ns")),
        ("1.5 us", (1.5, "us")),
        ("2µs", (2.0, "us")),
        ("3\tdt", (3.0, "dt")),
        ("1s", (1.0, "s")),
        ("4ms", (4.0, "ms")),
    ],
)
def test_timing_literals(source, value):
    assert kinds(source) == ["TIMING"]
    assert values(source) == [value]


def test_imaginary_literal():
    assert kinds("1.5im") == ["IMAGINARY"]
    assert values("2 im") == [2.0]


def test_unit_needs_a_word_boundary():
    # "2 sx" is the number 2 followed by the identifier "sx", not "2 s" and "x".
    assert kinds("2 sx") == ["INTEGER", "IDENTIFIER"]


@pytest.mark.parametrize("source", ["3x", "0b102", "12af", "3__4", "1e"])
def test_numbers_followed_by_letters_are_errors(source):
    with pytest.raises(QasmSyntaxError, match="invalid numeric literal"):
        tokenize(source)


def test_identifiers_and_keywords():
    assert kinds("qubit q_1 θ _x int") == ["qubit", "IDENTIFIER", "IDENTIFIER", "IDENTIFIER", "int"]
    assert kinds("true false") == ["true", "false"]
    assert values("true false") == [True, False]


def test_hardware_qubits():
    assert kinds("$0 $12") == ["HARDWARE_QUBIT", "HARDWARE_QUBIT"]
    assert values("$12") == [12]


@pytest.mark.parametrize("source", ["$", "$a", "$1a"])
def test_invalid_hardware_qubits(source):
    with pytest.raises(QasmSyntaxError):
        tokenize(source)


def test_operators_take_the_longest_match():
    assert kinds("**= ** * <<= << < -> - ++ +") == [
        "**=",
        "**",
        "*",
        "<<=",
        "<<",
        "<",
        "->",
        "-",
        "++",
        "+",
    ]


def test_version_after_openqasm():
    assert kinds("OPENQASM 3.1;") == ["OPENQASM", "VERSION", ";"]
    assert values("OPENQASM\n 3;")[1] == "3"
    with pytest.raises(QasmSyntaxError, match="version number"):
        tokenize("OPENQASM x;")


def test_pragma_and_annotation_take_the_rest_of_the_line():
    assert kinds("pragma foo bar  \nx") == ["pragma", "LINE_CONTENT", "IDENTIFIER"]
    assert values("#pragma a.b c")[1] == "a.b c"
    assert kinds("@ann.sub some text\nx") == ["ANNOTATION", "LINE_CONTENT", "IDENTIFIER"]
    assert values("@ann.sub some text")[0] == "ann.sub"
    assert kinds("@bare\n") == ["ANNOTATION"]


def test_at_followed_by_space_is_the_modifier_operator():
    assert kinds("ctrl @ x") == ["ctrl", "@", "IDENTIFIER"]


def test_calibration_blocks_are_opaque():
    tokens, _ = tokenize("cal { a { b } // c \n } x")
    assert [t.kind for t in tokens] == ["cal", "{", "CALIBRATION", "}", "IDENTIFIER", "EOF"]
    assert tokens[2].value == " a { b } // c \n "


def test_calibration_braces_are_counted_even_in_comments():
    # As in the specification grammar, the body is not interpreted at all.
    tokens, _ = tokenize("cal { // } \n }")
    assert [t.kind for t in tokens] == ["cal", "{", "CALIBRATION", "}", "}", "EOF"]


def test_defcal_body_starts_at_the_first_brace():
    tokens, _ = tokenize("defcal rx(angle a) $0 { play(a); }")
    assert [t.kind for t in tokens][-4:] == ["{", "CALIBRATION", "}", "EOF"]


def test_comments_are_collected():
    tokens, comments = tokenize("x; // one\n/* two\n */ y;")
    assert [t.kind for t in tokens] == ["IDENTIFIER", ";", "IDENTIFIER", ";", "EOF"]
    assert [c.text for c in comments] == ["// one", "/* two\n */"]
    assert comments[1].span.start_line == 2


def test_unterminated_constructs():
    with pytest.raises(QasmSyntaxError, match="block comment"):
        tokenize("/* never ends")
    with pytest.raises(QasmSyntaxError, match="string"):
        tokenize('include "abc\n";')
    with pytest.raises(QasmSyntaxError, match="calibration"):
        tokenize("cal { {")


def test_spans_use_zero_based_columns_and_exclusive_ends():
    tokens, _ = tokenize("qubit\n  abc;")
    assert tokens[1].span.start_line == 2
    assert (tokens[1].span.start_column, tokens[1].span.end_column) == (2, 5)


def test_byte_order_mark_is_ignored():
    assert kinds("﻿qubit q;") == ["qubit", "IDENTIFIER", ";"]


def test_unexpected_character():
    with pytest.raises(QasmSyntaxError, match="unexpected character '\\?'") as info:
        tokenize("x;\n  ?")
    assert info.value.span.start_line == 2
    assert info.value.span.start_column == 2
