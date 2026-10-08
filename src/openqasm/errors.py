"""Exceptions raised by the parser and the semantic analyser."""

from __future__ import annotations

from .ast import Span

__all__ = [
    "QasmError",
    "QasmSyntaxError",
    "QasmSemanticError",
    "QasmIncludeError",
    "UnsupportedVersionError",
]


class QasmError(Exception):
    """Base class of every error raised by this package.

    ``message`` is the bare description and ``span`` the offending source
    region, when known. ``str(error)`` prefixes the message with the location.
    """

    def __init__(self, message: str, span: Span | None = None):
        self.message = message
        self.span = span
        super().__init__(f"{span}: {message}" if span is not None else message)

    @property
    def line(self) -> int | None:
        return self.span.start_line if self.span else None

    @property
    def column(self) -> int | None:
        """0-based column where the error starts."""
        return self.span.start_column if self.span else None

    def render(self, source: str) -> str:
        """Format the error with the offending line and a caret marker.

        ``source`` must be the text the span refers to::

            program.qasm:3:7: error: undefined identifier 'q2'
              h q2[0];
                ^^
        """
        header = f"{self}" if self.span is None else f"{self.span}: error: {self.message}"
        if self.span is None:
            return header
        lines = source.splitlines()
        if not 1 <= self.span.start_line <= len(lines):
            return header
        text = lines[self.span.start_line - 1].expandtabs(1)
        start = self.span.start_column
        if self.span.end_line == self.span.start_line:
            width = max(1, self.span.end_column - start)
        else:
            width = max(1, len(text) - start)
        return f"{header}\n  {text}\n  {' ' * start}{'^' * width}"


class QasmSyntaxError(QasmError):
    """The source is not a syntactically valid program."""


class UnsupportedVersionError(QasmSyntaxError):
    """The ``OPENQASM`` version statement names a version this package does not support."""


class QasmSemanticError(QasmError):
    """The program is syntactically valid but breaks a rule of the language."""


class QasmIncludeError(QasmError):
    """An ``include`` statement names a file that cannot be found or read."""
