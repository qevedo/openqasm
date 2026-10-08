# openqasm

[![PyPI Version][pypi-badge]][pypi]
[![CI][ci-badge]][ci]
[![Python Versions][python-badge]][pypi]

Parse, check and print [OpenQASM 3](https://openqasm.com) programs in pure
Python, with no dependencies.

- **Parser**: the full OpenQASM 3.1 grammar, including the additions on the
  specification's `main` branch (`nop`, dotted annotations, measurement
  `defcal` calls). It produces a typed AST with source locations and keeps
  comments.
- **Semantic analysis**: scopes and visibility, `include` resolution
  (`stdgates.inc` built in), gate and subroutine signatures, broadcasting,
  constant evaluation of sizes, type compatibility, measurements, index bounds.
  Every problem is reported with its location.
- **Printer**: turns any tree back into source. `parse(dumps(p)) == p` holds
  for every program.
- **OpenQASM 2**: programs declaring `OPENQASM 2.0;` are parsed and checked
  with OpenQASM 2 semantics (`qelib1.inc`, `opaque`, `^` as power, `U`/`CX`),
  and printed back as OpenQASM 2.
- **Conformance**: the reference implementation's own test suite passes
  against this package (see [Conformance](#conformance)).

## Installation

```sh
pip install openqasm
```

Python 3.10 or newer. For the OpenQASM 2-only parser of earlier releases,
install `openqasm<3` (developed on the [`v2`](https://github.com/qevedo/openqasm/tree/v2) branch).

## Usage

```python
import openqasm

source = """
OPENQASM 3.1;
include "stdgates.inc";
qubit[2] q;
bit[2] c;
h q[0];
cx q[0], q[1];
c = measure q;
"""

program = openqasm.parse(source)          # syntax only -> openqasm.ast.Program
analysis = openqasm.analyze(program)      # names, types, signatures, includes
assert analysis.ok, analysis.errors
print(analysis.qubits)                    # {'q': 2}
print(openqasm.dumps(program))            # canonical source text
```

`loads` and `load` parse and check in one step, raising the first error (all
of them are in its `errors` attribute):

```python
program = openqasm.load("teleport.qasm", include_paths=["lib/"])
```

Errors carry a `span` and can render themselves:

```python
try:
    openqasm.loads("qubit q;\nh q;")
except openqasm.QasmError as error:
    print(error.render("qubit q;\nh q;"))
# 2:1: error: undefined gate 'h'
#   h q;
#   ^
```

### The tree

Nodes are dataclasses in `openqasm.ast`. They compare by content: spans,
comments and cosmetic choices such as `qreg` vs `qubit[n]` are ignored by
equality. Walk or rewrite trees with `openqasm.walk`, `openqasm.Visitor` and
`openqasm.Transformer`:

```python
from openqasm import ast, Transformer

class RenameRegister(Transformer):
    def visit_Identifier(self, node):
        return ast.Identifier("data") if node.name == "q" else node

print(openqasm.dumps(RenameRegister().visit(program)))
```

### Printing options

`openqasm.dumps(node, indent="  ", chain_else_if=True, old_measurement=False)`
prints a program, a statement, an expression or a type. `old_measurement`
prints every measurement as `measure q -> c;`.

### Command line

```console
$ openqasm check teleport.qasm -I lib/
teleport.qasm: ok
$ openqasm format messy.qasm > tidy.qasm
```

`check` exits with status 1 if a program has errors. `format` does not keep
comments and warns when a file has some.

## What the analyser checks

- names are declared before use, are not redeclared in the same scope, and
  are visible where they are used (gates and subroutines only see constants,
  gates and subroutines from outside);
- gate calls: the gate exists, parameter and qubit counts (including
  `ctrl`/`negctrl` modifiers), distinct qubits, broadcasting over registers of
  equal size, no recursion, no indexing of gate qubit arguments;
- subroutine and built-in function calls: argument counts and kinds, return
  values;
- sizes and designators are positive constant expressions, constant
  initialisers are constant, constant indices are in range;
- assignments and initialisers have compatible types; constants, inputs and
  qubits are not assigned;
- measurement results fit their targets, durations are used as durations,
  conditions are classical, `switch` cases are distinct constant integers.

The analyser reports clear violations of the specification only. Anything
whose validity depends on run-time values, or that the specification leaves
to implementations, is accepted.

## Conformance

The OpenQASM project publishes a reference Python package, `openqasm3`,
built on the ANTLR grammar of the specification. `tests/conformance/run.sh`
checks this package against it:

- the reference test suite runs unmodified against our parser and printer
  (672 tests pass; 2 are expected differences, listed below);
- a differential test parses the whole specification corpus with both
  parsers: example programs, grammar reference files, invalid statements and
  every code block of the specification text (292 inputs, all agree);
- the specification's example programs pass semantic analysis, except five
  that contain mistakes, which are reported.

Deliberate differences from the reference implementation:

| | `openqasm3` | `openqasm` |
|---|---|---|
| Dependencies | ANTLR runtime pinned to the generator version | none |
| Semantic analysis | none | scopes, types, signatures, includes |
| OpenQASM 2 programs | rejected | parsed and checked |
| Hardware qubits | `Identifier("$0")` | `HardwareQubit(0)` |
| `qreg`/`creg`, `measure ->` | normalised away | kept for printing |
| Comments | separate `get_comments()` | `Program.comments` |
| `qubit` declared in `{ }` or `switch` blocks | accepted | rejected (spec: global only) |
| Empty or comment-only programs, `def f(creg c[2])` | crash | parsed |

See [tests/conformance/README.md](tests/conformance/README.md) for details.

## Development

```sh
pip install -e ".[test]"
pytest                          # unit tests
tests/conformance/run.sh        # conformance suite (needs Java for ANTLR)
```

Branches and releases are described in [docs/BRANCHING.md](docs/BRANCHING.md).

## License

MIT. `src/openqasm/stdlib/stdgates.inc` and `qelib1.inc` are the standard
libraries of the OpenQASM specification and stay under Apache 2.0 (see
`src/openqasm/stdlib/LICENSE`).

[pypi-badge]: https://img.shields.io/pypi/v/openqasm
[pypi]: https://pypi.org/project/openqasm
[ci-badge]: https://github.com/qevedo/openqasm/actions/workflows/ci.yml/badge.svg?branch=master
[ci]: https://github.com/qevedo/openqasm/actions/workflows/ci.yml
[python-badge]: https://img.shields.io/pypi/pyversions/openqasm
