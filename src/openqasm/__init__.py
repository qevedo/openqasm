"""Parse, analyse and print OpenQASM 3 programs.

Quick start::

    import openqasm

    program = openqasm.parse(source)       # syntax only
    openqasm.analyze(program).errors       # semantic problems, if any
    program = openqasm.load("bell.qasm")   # read, parse and check a file
    print(openqasm.dumps(program))         # back to source text
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version
from typing import Union

from . import ast
from .errors import (
    QasmError,
    QasmIncludeError,
    QasmSemanticError,
    QasmSyntaxError,
    UnsupportedVersionError,
)
from .parser import SUPPORTED_VERSIONS, parse, parse_expression
from .printer import dump, dumps
from .semantic import Analysis, analyze
from .visitor import Transformer, Visitor, walk

try:
    __version__ = _version("openqasm")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0+unknown"

__all__ = [
    "ast",
    "parse",
    "parse_expression",
    "analyze",
    "loads",
    "load",
    "dumps",
    "dump",
    "walk",
    "Visitor",
    "Transformer",
    "Analysis",
    "SUPPORTED_VERSIONS",
    "QasmError",
    "QasmSyntaxError",
    "QasmSemanticError",
    "QasmIncludeError",
    "UnsupportedVersionError",
]

PathLike = Union[str, "os.PathLike[str]"]


def loads(
    source: str,
    *,
    filename: str | None = None,
    include_paths: Sequence[PathLike] = (),
) -> ast.Program:
    """Parse ``source`` and check it, raising the first error found.

    The raised :class:`QasmError` has an ``errors`` attribute listing every
    semantic error when there are several.
    """
    program = parse(source, filename=filename)
    analyze(program, filename=filename, include_paths=include_paths).raise_for_errors()
    return program


def load(path: PathLike, *, include_paths: Sequence[PathLike] = ()) -> ast.Program:
    """Read, parse and check the program in the file ``path``."""
    filename = os.fspath(path)
    with open(filename, encoding="utf-8") as file:
        source = file.read()
    return loads(source, filename=filename, include_paths=include_paths)
