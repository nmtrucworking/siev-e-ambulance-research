#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT_PYTHON="${PROJECT_PYTHON:-python3.13}"
if ! command -v "$PROJECT_PYTHON" >/dev/null 2>&1; then
  echo "Required interpreter not found: $PROJECT_PYTHON" >&2
  exit 1
fi

"$PROJECT_PYTHON" -m venv --clear .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c 'import sys; print("Environment ready:", sys.version.split()[0])'
