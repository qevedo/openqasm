# openqasm

[![PyPI Version][openqasm-pi]][openqasm-pu]
[![Build Status][openqasm-ti]][openqasm-tu]
[![Dependency Status][openqasm-di]][openqasm-du]
[![Python Version][python-vi]][python-vu]

An OPENQASM 2.0 parser for Python

## Installation

```sh
pip install openqasm
```

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

## License

Apache 2.0

[openqasm-pi]: https://img.shields.io/pypi/v/openqasm
[openqasm-pu]: https://pypi.org/project/openqasm
[openqasm-ti]: https://img.shields.io/travis/com/qevedo/openqasm/master.svg
[openqasm-tu]: https://travis-ci.com/qevedo/openqasm
[openqasm-di]: https://img.shields.io/librariesio/github/qevedo/openqasm
[openqasm-du]: https://pypi.org/project/openqasm
[python-vi]: https://img.shields.io/badge/python-3.5-blue.svg
[python-vu]: https://python.org/downloads/release/python-350/
