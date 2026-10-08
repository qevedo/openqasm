# Conformance suite

These tests compare `openqasm` with the reference implementation published by
the OpenQASM project, [`openqasm3`](https://github.com/openqasm/openqasm/tree/main/source/openqasm),
whose ANTLR grammar is the grammar of the specification. Run them with:

```sh
tests/conformance/run.sh        # needs git, curl, Java and a Python >= 3.10 environment
```

The script checks out `openqasm/openqasm` at a pinned commit (`OPENQASM_REF`),
generates the reference parser with ANTLR and runs three suites:

| Suite | What it checks |
|-------|----------------|
| `source/openqasm/tests` (upstream, unmodified) | The reference test suite, run through `upstream_plugin.py`, which swaps the reference parser and printer for ours via `reference_adapter.py`. |
| `test_differential.py` | Both parsers on the specification corpus: example programs, grammar reference files, invalid statements and every code block of the specification text. Both must accept or reject each input, and accepted inputs must give the same tree. |
| `test_examples_semantics.py` | `openqasm.analyze` on the example programs: all valid ones pass, and the known mistakes in the others are reported. |

## Expected differences

`upstream_plugin.py` marks the upstream tests whose expectations we
deliberately do not meet:

- `test_rejects_invalid_version[2.0]`: OpenQASM 2.0 programs are accepted.
- `test_non_integer_physical_qubit_raises`: the test matches the exact wording
  of an ANTLR error; `$a` is rejected with a different message.

Two tests of the reference helpers `parse_version()` and
`spec.supported_versions` are deselected because they never reach our code.

## Reference bugs found by the differential tests

The reference parser crashes (it raises `AttributeError`, not a parse error)
on inputs that are valid OpenQASM, and `openqasm` parses them:

- empty programs and programs containing only comments;
- subroutine arguments in the OpenQASM 2 form `def f(creg c[2]) { }`.

It also accepts a `qubit` declaration inside a bare `{ }` block or a `switch`
case, which the specification forbids (qubits are declared in the global scope only).

Five example programs of the specification contain mistakes, listed in
`test_examples_semantics.py`.
