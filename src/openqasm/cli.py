"""Command-line interface: ``openqasm check`` and ``openqasm format``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__
from .errors import QasmError
from .parser import parse
from .printer import dumps
from .semantic import analyze


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as file:
        return file.read()


def _check(args: argparse.Namespace) -> int:
    failed = False
    for path in args.files:
        filename = None if path == "-" else path
        source = _read(path)
        try:
            program = parse(source, filename=filename)
        except QasmError as error:
            print(error.render(source), file=sys.stderr)
            failed = True
            continue
        analysis = analyze(program, filename=filename, include_paths=args.include)
        for problem in analysis.errors:
            same_file = problem.span is not None and problem.span.file == filename
            print(problem.render(source) if same_file else str(problem), file=sys.stderr)
        if analysis.errors:
            failed = True
        elif not args.quiet:
            print(f"{path}: ok")
    return 1 if failed else 0


def _format(args: argparse.Namespace) -> int:
    status = 0
    for path in args.files:
        source = _read(path)
        try:
            program = parse(source, filename=None if path == "-" else path)
        except QasmError as error:
            print(error.render(source), file=sys.stderr)
            status = 1
            continue
        if program.comments:
            print(f"{path}: warning: comments are not preserved by the formatter", file=sys.stderr)
        indent = "\t" if args.tabs else " " * args.indent
        sys.stdout.write(dumps(program, indent=indent))
    return status


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openqasm", description="OpenQASM 3 tools.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="parse and check programs for errors")
    check.add_argument("files", nargs="+", metavar="FILE", help="programs to check ('-' for stdin)")
    check.add_argument(
        "-I",
        "--include",
        action="append",
        default=[],
        metavar="DIR",
        help="directory searched by include statements (repeatable)",
    )
    check.add_argument("-q", "--quiet", action="store_true", help="only print errors")
    check.set_defaults(run=_check)

    format_ = commands.add_parser("format", help="print programs in canonical form")
    format_.add_argument(
        "files", nargs="+", metavar="FILE", help="programs to format ('-' for stdin)"
    )
    format_.add_argument("--indent", type=int, default=2, help="spaces per indentation level")
    format_.add_argument("--tabs", action="store_true", help="indent with tabs")
    format_.set_defaults(run=_format)

    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except OSError as error:
        print(f"openqasm: {error.filename}: {error.strerror}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
