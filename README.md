# openqasm

[![PyPI Version][openqasm-pi]][openqasm-pu]
[![Build Status][openqasm-ti]][openqasm-tu]
[![Dependency Status][openqasm-di]][openqasm-du]
[![Python Version][python-vi]][python-vu]

An OpenQASM 2.0 parser for Python.

> This is the `v2` branch, which holds the **OpenQASM 2** parser
> (`openqasm` 2.x on PyPI). OpenQASM 3 lives on `master` (`openqasm` 3.x).

## Installation

```sh
pip install "openqasm<3"
```

To export expressions to LaTeX, install the optional extra:
`pip install "openqasm[latex]<3"`.

## Usage

```python
from openqasm import Qasm

file_path = '/path/to/file.qasm'

# Parse the QASM file
qasm = Qasm(file_path)

# Get AST
ast = qasm.parse()

# Print the parsed QASM with precision=15
print(ast.qasm(15))
```

The bundled `qelib1.inc` is the final version of the IBM/Qiskit standard
header. It contains every gate of the specification's `qelib1.inc` plus newer
gates (`sx`, `rxx`, `cu`, ...). Programs that define those newer gates
themselves keep working: their definition replaces the bundled one.

## Development

```sh
pip install -e ".[test,latex]"
pytest
```

See [CHANGELOG.md](CHANGELOG.md) for changes.

## License

Apache 2.0

[openqasm-pi]: https://img.shields.io/github/v/tag/qevedo/openqasm?filter=v2.*&label=pypi&cacheSeconds=3600
[openqasm-pu]: https://pypi.org/project/openqasm/#history
[openqasm-ti]: https://github.com/qevedo/openqasm/actions/workflows/ci.yml/badge.svg?branch=v2
[openqasm-tu]: https://github.com/qevedo/openqasm/actions/workflows/ci.yml
[openqasm-di]: https://img.shields.io/librariesio/github/qevedo/openqasm
[openqasm-du]: https://pypi.org/project/openqasm
[python-vi]: https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fqevedo%2Fopenqasm%2Fv2%2Fpyproject.toml&cacheSeconds=3600
[python-vu]: https://pypi.org/project/openqasm
