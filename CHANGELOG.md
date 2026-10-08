# Changelog

`openqasm` 3.x implements OpenQASM 3 and is developed on `master`. The
OpenQASM 2-only parser (`openqasm` 2.x) is maintained on the `v2` branch,
which has its own changelog.

## 3.0.0

A new implementation for OpenQASM 3, written from scratch. It shares no code
or API with 2.x: replace `Qasm(path).parse()` with `openqasm.load(path)` (or
`openqasm.parse(source)`), and `node.qasm()` with `openqasm.dumps(node)`.

- Parser for the OpenQASM 3.1 grammar plus the additions on the
  specification's `main` branch, written by hand with no dependencies. Typed
  dataclass AST with source spans; comments are kept in `Program.comments`.
- Semantic analyser (`openqasm.analyze`): scopes and visibility, includes,
  gate/subroutine/function signatures, broadcasting, constant evaluation,
  type compatibility, measurements and index bounds.
- Printer (`openqasm.dumps`) with minimal parenthesisation; parsing what it
  prints gives back the same tree.
- OpenQASM 2 compatibility: `OPENQASM 2.0;` programs are parsed and checked
  with `qelib1.inc`, `opaque`, `^` as exponentiation and the built-in `CX`,
  and printed back as OpenQASM 2.
- `walk`, `Visitor` and `Transformer` for traversing and rewriting trees.
- `openqasm check` and `openqasm format` commands.
- Conformance suite against the reference implementation of the OpenQASM
  project: its test suite, a differential test over the specification corpus,
  and semantic checks of the specification examples.
- Requires Python 3.10 or newer.
