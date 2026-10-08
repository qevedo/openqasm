#!/usr/bin/env bash
# Run the conformance suite against the reference implementation of the
# OpenQASM project (https://github.com/openqasm/openqasm).
#
#   tests/conformance/run.sh [pytest options]
#
# The script checks out the OpenQASM repository at a pinned commit into
# .conformance/, generates the reference ANTLR parser (needs Java), installs
# the reference package next to this one in the current Python environment,
# and runs:
#   1. the reference test suite, with its parser and printer replaced by ours;
#   2. differential tests comparing both parsers on the specification corpus;
#   3. semantic checks of the specification's example programs.
set -euo pipefail

OPENQASM_REF="${OPENQASM_REF:-7fbf9e9eb3692a1288c014d6efd43523701886c6}"
ANTLR_VERSION="4.13.2"
PYTHON="${PYTHON:-python}"

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work="${CONFORMANCE_DIR:-$root/.conformance}"
repo="$work/openqasm"
jar="$work/antlr-$ANTLR_VERSION-complete.jar"
mkdir -p "$work"

if [ ! -d "$repo/.git" ]; then
    git clone --quiet https://github.com/openqasm/openqasm.git "$repo"
fi
git -C "$repo" fetch --quiet origin "$OPENQASM_REF" 2>/dev/null || true
git -C "$repo" checkout --quiet "$OPENQASM_REF"

if [ ! -f "$jar" ]; then
    curl -sSfL -o "$jar" \
        "https://repo1.maven.org/maven2/org/antlr/antlr4/$ANTLR_VERSION/antlr4-$ANTLR_VERSION-complete.jar"
fi
generated="$repo/source/openqasm/openqasm3/_antlr/_${ANTLR_VERSION%.*}"
generated="${generated//./_}"
if [ ! -d "$generated" ]; then
    (cd "$repo/source/grammar" && java -jar "$jar" -o "$generated" -Dlanguage=Python3 -visitor \
        qasm3Lexer.g4 qasm3Parser.g4)
fi

"$PYTHON" -m pip install --quiet "antlr4-python3-runtime==$ANTLR_VERSION" pyyaml pytest
"$PYTHON" -m pip install --quiet --no-deps -e "$repo/source/openqasm"
"$PYTHON" -m pip install --quiet -e "$root"

export PYTHONPATH="$root/tests/conformance${PYTHONPATH:+:$PYTHONPATH}"
export OPENQASM_REPO="$repo"
cd "$work"
"$PYTHON" -m pytest -p no:cacheprovider -p upstream_plugin "$repo/source/openqasm/tests" "$@"
"$PYTHON" -m pytest -p no:cacheprovider "$root/tests/conformance" "$@"
