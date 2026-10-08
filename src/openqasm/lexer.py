"""Tokenizer for OpenQASM 3 source text.

The tokenizer follows the lexer grammar of the specification, including its
context-dependent parts:

* the version number after ``OPENQASM``;
* the rest of the line after ``pragma`` and after an annotation ``@name``;
* the verbatim body of ``cal { ... }`` and ``defcal ... { ... }`` blocks.

It is stricter in one way: a number immediately followed by letters (``3x``,
``0b102``) is an error at the number rather than two tokens.
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from .ast import Comment, Span
from .errors import QasmSyntaxError

__all__ = ["Token", "tokenize", "KEYWORDS", "TYPE_KEYWORDS"]

KEYWORDS = frozenset(
    [
        "OPENQASM",
        "include",
        "defcalgrammar",
        "def",
        "cal",
        "defcal",
        "gate",
        "extern",
        "box",
        "let",
        "break",
        "continue",
        "if",
        "else",
        "end",
        "return",
        "for",
        "while",
        "in",
        "switch",
        "case",
        "default",
        "nop",
        "pragma",
        "input",
        "output",
        "const",
        "readonly",
        "mutable",
        "qreg",
        "qubit",
        "creg",
        "bool",
        "bit",
        "int",
        "uint",
        "float",
        "angle",
        "complex",
        "array",
        "void",
        "duration",
        "stretch",
        "gphase",
        "inv",
        "pow",
        "ctrl",
        "negctrl",
        "durationof",
        "delay",
        "reset",
        "measure",
        "barrier",
        "true",
        "false",
        "im",
    ]
)

#: Keywords that start a classical scalar or array type.
TYPE_KEYWORDS = frozenset(
    ["bool", "bit", "int", "uint", "float", "angle", "complex", "duration", "stretch", "array"]
)

# Longest operators first so that the first match is the longest.
_OPERATORS = sorted(
    [
        "**=",
        "<<=",
        ">>=",
        "**",
        "++",
        "->",
        "==",
        "!=",
        ">=",
        "<=",
        "<<",
        ">>",
        "&&",
        "||",
        "+=",
        "-=",
        "*=",
        "/=",
        "&=",
        "|=",
        "^=",
        "%=",
        "[",
        "]",
        "{",
        "}",
        "(",
        ")",
        ":",
        ";",
        ".",
        ",",
        "=",
        "+",
        "-",
        "*",
        "/",
        "%",
        "|",
        "&",
        "^",
        "@",
        "~",
        "!",
        "<",
        ">",
    ],
    key=len,
    reverse=True,
)
_OPERATOR_FIRST = frozenset(op[0] for op in _OPERATORS)

_DECIMAL = r"[0-9](?:_?[0-9])*"
_EXPONENT = rf"[eE][+-]?{_DECIMAL}"
_FLOAT = (
    rf"(?:{_DECIMAL}{_EXPONENT}"  # 1e3
    rf"|\.{_DECIMAL}(?:{_EXPONENT})?"  # .5, .5e3
    rf"|{_DECIMAL}\.(?:{_DECIMAL})?(?:{_EXPONENT})?)"  # 1., 1.5, 1.5e3
)
_NUMBER_RE = re.compile(
    rf"""
    (?P<binary>0[bB][01](?:_?[01])*)
    | (?P<octal>0o[0-7](?:_?[0-7])*)
    | (?P<hex>0[xX][0-9a-fA-F](?:_?[0-9a-fA-F])*)
    | (?P<float>{_FLOAT})
    | (?P<decimal>{_DECIMAL})
    """,
    re.VERBOSE,
)
# Suffix turning a number into an imaginary or timing literal. Spaces and
# tabs may separate the number and the suffix.
_SUFFIX_RE = re.compile(r"[ \t]*(im|dt|ns|us|µs|μs|ms|s)(?![\w])")
_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_IDENTIFIER_RE = re.compile(r"[^\W\d]\w*")
_BITSTRING_RE = re.compile(r"[01](?:_?[01])*")
_WHITESPACE = " \t\r\n\f\v"

_ID_START_CATEGORIES = frozenset({"Lu", "Ll", "Lt", "Lm", "Lo", "Nl"})


def _is_id_start(char: str) -> bool:
    return char == "_" or unicodedata.category(char) in _ID_START_CATEGORIES


def _is_id_char(char: str) -> bool:
    return "0" <= char <= "9" or _is_id_start(char)


@dataclass(frozen=True)
class Token:
    """A token. ``kind`` is the keyword or operator text for those tokens, and one
    of ``IDENTIFIER``, ``INTEGER``, ``FLOAT``, ``IMAGINARY``, ``TIMING``,
    ``STRING``, ``HARDWARE_QUBIT``, ``VERSION``, ``LINE_CONTENT``, ``ANNOTATION``,
    ``CALIBRATION``, ``DIM`` or ``EOF`` otherwise."""

    kind: str
    text: str
    value: Any
    span: Span
    start: int
    end: int

    def __repr__(self) -> str:
        return (
            f"Token({self.kind!r}, {self.text!r}, {self.span.start_line}:{self.span.start_column})"
        )


class _Lexer:
    def __init__(self, source: str, filename: str | None):
        if source.startswith("﻿"):
            source = source[1:]
        self.source = source
        self.filename = filename
        self.pos = 0
        self.tokens: list[Token] = []
        self.comments: list[Comment] = []
        self._line_starts = [0] + [m.end() for m in re.finditer("\n", source)]
        self._defcal_pending = False

    # -- helpers ----------------------------------------------------------

    def location(self, offset: int) -> tuple[int, int]:
        line = bisect.bisect_right(self._line_starts, offset)
        return line, offset - self._line_starts[line - 1]

    def span(self, start: int, end: int) -> Span:
        start_line, start_column = self.location(start)
        end_line, end_column = self.location(end)
        return Span(start_line, start_column, end_line, end_column, self.filename)

    def error(self, message: str, start: int, end: int | None = None) -> QasmSyntaxError:
        return QasmSyntaxError(message, self.span(start, max(end or start + 1, start + 1)))

    def emit(self, kind: str, start: int, end: int, value: Any = None) -> Token:
        token = Token(kind, self.source[start:end], value, self.span(start, end), start, end)
        self.tokens.append(token)
        return token

    def skip_whitespace(self, newlines: bool = True) -> None:
        source, pos = self.source, self.pos
        chars = _WHITESPACE if newlines else " \t"
        while pos < len(source) and source[pos] in chars:
            pos += 1
        self.pos = pos

    def skip_trivia(self) -> None:
        """Skip whitespace and comments, recording the comments."""
        source = self.source
        while True:
            self.skip_whitespace()
            if source.startswith("//", self.pos):
                end = source.find("\n", self.pos)
                end = len(source) if end < 0 else end
                if source[end - 1 : end] == "\r":
                    end -= 1
                self.comments.append(Comment(source[self.pos : end], span=self.span(self.pos, end)))
                self.pos = end
            elif source.startswith("/*", self.pos):
                end = source.find("*/", self.pos + 2)
                if end < 0:
                    raise self.error("unterminated block comment", self.pos, self.pos + 2)
                end += 2
                self.comments.append(Comment(source[self.pos : end], span=self.span(self.pos, end)))
                self.pos = end
            else:
                return

    # -- main loop ---------------------------------------------------------

    def run(self) -> list[Token]:
        source = self.source
        while True:
            self.skip_trivia()
            if self.pos >= len(source):
                self.emit("EOF", self.pos, self.pos)
                return self.tokens
            char = source[self.pos]
            if (
                char.isdigit()
                and char.isascii()
                or (char == "." and self.pos + 1 < len(source) and source[self.pos + 1].isdigit())
            ):
                self.number()
            elif _is_id_start(char) or char == "_":
                self.word()
            elif char in "\"'":
                self.string()
            elif char == "$":
                self.hardware_qubit()
            elif char == "#":
                self.hash()
            elif char == "@" and self.pos + 1 < len(source) and _is_id_start(source[self.pos + 1]):
                self.annotation()
            elif char in _OPERATOR_FIRST:
                self.operator()
            else:
                raise self.error(f"unexpected character {char!r}", self.pos)

    def number(self) -> None:
        start = self.pos
        match = _NUMBER_RE.match(self.source, start)
        assert match is not None
        kind = match.lastgroup
        text = match.group().replace("_", "")
        if kind == "binary":
            value: Any = int(text[2:], 2)
        elif kind == "octal":
            value = int(text[2:], 8)
        elif kind == "hex":
            value = int(text[2:], 16)
        elif kind == "float":
            value = float(text)
        else:
            value = int(text)
        end = match.end()
        token_kind = "FLOAT" if kind == "float" else "INTEGER"
        if kind in ("float", "decimal"):
            suffix = _SUFFIX_RE.match(self.source, end)
            if suffix is not None:
                unit = suffix.group(1)
                end = suffix.end()
                if unit == "im":
                    token_kind, value = "IMAGINARY", float(value)
                else:
                    unit = "us" if unit in ("µs", "μs") else unit
                    token_kind, value = "TIMING", (float(value), unit)
        if end < len(self.source) and _is_id_char(self.source[end]):
            stop = end
            while stop < len(self.source) and _is_id_char(self.source[stop]):
                stop += 1
            raise self.error(f"invalid numeric literal {self.source[start:stop]!r}", start, stop)
        self.pos = end
        self.emit(token_kind, start, end, value)

    def identifier_end(self, start: int) -> int:
        match = _IDENTIFIER_RE.match(self.source, start)
        end = start + 1 if match is None else match.end()
        # ``\w`` is wider than the grammar's identifier characters; trim.
        stop = start + 1
        while stop < end and _is_id_char(self.source[stop]):
            stop += 1
        return stop

    def word(self) -> None:
        start = self.pos
        end = self.pos = self.identifier_end(start)
        text = self.source[start:end]
        if text not in KEYWORDS:
            self.emit("IDENTIFIER", start, end, text)
            return
        if text in ("true", "false"):
            self.emit(text, start, end, text == "true")
            return
        self.emit(text, start, end)
        if text == "OPENQASM":
            self.version()
        elif text == "pragma":
            self.line_content()
        elif text == "cal":
            self.skip_trivia()
            if not self.source.startswith("{", self.pos):
                raise self.error("expected '{' after 'cal'", self.pos)
            self.calibration_body()
        elif text == "defcal":
            self._defcal_pending = True

    def version(self) -> None:
        self.skip_whitespace()
        match = _VERSION_RE.match(self.source, self.pos)
        if match is None:
            raise self.error("expected a version number after 'OPENQASM'", self.pos)
        self.pos = match.end()
        self.emit("VERSION", match.start(), match.end(), match.group())

    def line_content(self) -> None:
        """Consume the rest of the line after ``pragma`` or an annotation keyword."""
        self.skip_whitespace(newlines=False)
        start = self.pos
        end = self.source.find("\n", start)
        end = len(self.source) if end < 0 else end
        if self.source[end - 1 : end] == "\r" and end - 1 >= start:
            end -= 1
        self.pos = end
        if end > start:
            self.emit("LINE_CONTENT", start, end, self.source[start:end])

    def annotation(self) -> None:
        start = self.pos
        end = self.identifier_end(start + 1)
        while (
            self.source.startswith(".", end)
            and end + 1 < len(self.source)
            and _is_id_start(self.source[end + 1])
        ):
            end = self.identifier_end(end + 1)
        self.pos = end
        self.emit("ANNOTATION", start, end, self.source[start + 1 : end])
        self.line_content()

    def string(self) -> None:
        start = self.pos
        quote = self.source[start]
        pos = start + 1
        while pos < len(self.source) and self.source[pos] != quote:
            if self.source[pos] in "\r\n":
                break
            pos += 1
        if pos >= len(self.source) or self.source[pos] != quote:
            raise self.error("unterminated string literal", start, pos)
        self.pos = pos + 1
        self.emit("STRING", start, self.pos, self.source[start + 1 : pos])

    def hardware_qubit(self) -> None:
        start = self.pos
        end = start + 1
        while end < len(self.source) and self.source[end].isascii() and self.source[end].isdigit():
            end += 1
        if end == start + 1:
            raise self.error("expected a qubit number after '$'", start)
        if end < len(self.source) and _is_id_char(self.source[end]):
            raise self.error("invalid hardware qubit", start, end + 1)
        self.pos = end
        self.emit("HARDWARE_QUBIT", start, end, int(self.source[start + 1 : end]))

    def hash(self) -> None:
        start = self.pos
        for word, kind in (("#pragma", "pragma"), ("#dim", "DIM")):
            end = start + len(word)
            if self.source.startswith(word, start) and not (
                end < len(self.source) and _is_id_char(self.source[end])
            ):
                self.pos = end
                self.emit(kind, start, end)
                if kind == "pragma":
                    self.line_content()
                return
        raise self.error("unexpected character '#'", start)

    def operator(self) -> None:
        start = self.pos
        for op in _OPERATORS:
            if self.source.startswith(op, start):
                self.pos = start + len(op)
                self.emit(op, start, self.pos)
                if op == "{" and self._defcal_pending:
                    self._defcal_pending = False
                    self.pos = start
                    self.tokens.pop()
                    self.calibration_body()
                return
        raise self.error(f"unexpected character {self.source[start]!r}", start)

    def calibration_body(self) -> None:
        """Lex ``{ body }`` where the body is opaque text with balanced braces."""
        open_brace = self.pos
        self.emit("{", open_brace, open_brace + 1)
        depth = 0
        pos = open_brace + 1
        while pos < len(self.source):
            char = self.source[pos]
            if char == "{":
                depth += 1
            elif char == "}":
                if depth == 0:
                    break
                depth -= 1
            pos += 1
        else:
            raise self.error("unterminated calibration block", open_brace)
        if pos > open_brace + 1:
            self.emit("CALIBRATION", open_brace + 1, pos, self.source[open_brace + 1 : pos])
        self.pos = pos + 1
        self.emit("}", pos, pos + 1)


def tokenize(source: str, filename: str | None = None) -> tuple[list[Token], list[Comment]]:
    """Split ``source`` into tokens, ending with an ``EOF`` token, and comments."""
    lexer = _Lexer(source, filename)
    return lexer.run(), lexer.comments


def is_bitstring(text: str) -> bool:
    """Whether ``text`` (without quotes) is a valid bit-string literal body."""
    return _BITSTRING_RE.fullmatch(text) is not None
