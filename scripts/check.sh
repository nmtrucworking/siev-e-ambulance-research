#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=.venv/bin/python
if [[ ! -x "$PYTHON" ]]; then
  echo "Missing .venv. Run ./scripts/bootstrap.sh first." >&2
  exit 1
fi

"$PYTHON" -m compileall -q src
"$PYTHON" src/validate_dataset.py

for EXP in experiments/smoke_1rep experiments/smoke_3rep; do
  if [[ -d "$EXP" ]]; then
    "$PYTHON" src/validate_runtime_outputs.py --experiment "$EXP"
  fi
done
