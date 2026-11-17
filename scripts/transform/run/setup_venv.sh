#!/usr/bin/env bash
# Purpose:   Create the server venv for the transform runtime (requirements-transform.txt: duckdb, langcodes, pyarrow).
# Usage:     bash scripts/transform/run/setup_venv.sh
#            Run from the gemea project root. The full local env uses requirements.txt instead.
# Outputs:   .venv/ in the project root
# Deps:      python3, pip

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"   # scripts/transform/run/ → gemea root
VENV="$ROOT/.venv"

python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install -r "$ROOT/requirements-transform.txt"

echo "Venv ready: $VENV"
echo "Python: $("$VENV/bin/python3" --version)"
echo "duckdb: $("$VENV/bin/python3" -c 'import duckdb; print(duckdb.__version__)')"
