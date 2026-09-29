#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Validated Python environment not found: $PYTHON_BIN" >&2
  exit 2
fi

export PYTHONHASHSEED=0

"$PYTHON_BIN" src/pipeline_lock.py verify --root "$ROOT" --lock provenance/final_pipeline_lock.json

"$PYTHON_BIN" src/run_experiment.py \
  --root . \
  --output experiments/paired_30 \
  --replications-per-scenario 30 \
  --policies B0 B3 B4 B6 \
  --workers "${PAIRED_WORKERS:-4}" \
  --prefetch-routing \
  --overwrite
"$PYTHON_BIN" src/validate_runtime_outputs.py --experiment experiments/paired_30
"$PYTHON_BIN" src/analyze_paired.py \
  --input experiments/paired_30/replication_metrics.csv \
  --output experiments/paired_30/analysis
