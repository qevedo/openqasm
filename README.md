# openqasm

[![Build Status][openqasm-ti]][openqasm-tu]

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

[openqasm-ti]: https://img.shields.io/travis/com/qevedo/openqasm/master.svg
[openqasm-tu]: https://travis-ci.com/qevedo/openqasm
