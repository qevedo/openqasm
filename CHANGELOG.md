# Changelog

This branch (`v2`) contains the **OpenQASM 2.0** parser. It is published to
PyPI as `openqasm` 2.x. The OpenQASM 3 implementation is developed on `master`
and published as `openqasm` 3.x.

## 2.0.0

Brings the parser up to date with the last upstream changes to the OpenQASM 2
reference implementation (the `qiskit.qasm` module, final release in Qiskit
0.46), plus fixes found along the way.

### Changed

- The package version now follows the language major version: `openqasm` 2.x
  parses OpenQASM 2, `openqasm` 3.x parses OpenQASM 3. Pin `openqasm<3` to stay
  on this line.
- `qelib1.inc` is the final upstream version, adding `u`, `p`, `sx`, `sxdg`,
  `crx`, `cry`, `cp`, `csx`, `cu`, `rxx`, `rzz`, `rccx`, `rc3x`, `c3x`,
  `c3sqrtx` and `c4x`; `cu3` now includes the control phase that was missing
  and `crz` is defined with `rz`.
- Programs may define the gates that `qelib1.inc` added after the
  specification (for example their own `cu` or `sx`); the user definition
  replaces the bundled one. Gates of the specification's `qelib1.inc` still
  cannot be redefined.
- Includes are resolved relative to the including file before the current
  working directory.
- Packaging moved to `pyproject.toml`. Runtime dependencies (`ply`, `numpy`)
  are now declared, so `pip install openqasm` works on its own, and versions
  are no longer pinned. `pylatexenc` is optional (`pip install openqasm[latex]`).
- Requires Python 3.8 or newer.

### Fixed

- `OPENQASM 2;` (version without minor number) is accepted.
- Versions other than 2.0 are rejected with
  `Invalid version: 'X.Y'. This module supports OpenQASM 2.0 only.` instead of
  being parsed silently.
- Illegal characters raise `QasmError` with the line and file instead of
  printing to stdout and raising a PLY `LexError`.
- Syntax errors that PLY recovered from no longer print to stdout and are
  reported as a `QasmError` with line and column.
- `Real.real()` crashed with `AttributeError`.
- `Qasm(data=...).generate_tokens()` returned no tokens.
- Tokenizer errors from `generate_tokens()` are raised instead of printed.
- The test suite no longer depends on the working directory.

## 0.1.0

First release: OpenQASM 2.0 parser forked from Qiskit.
