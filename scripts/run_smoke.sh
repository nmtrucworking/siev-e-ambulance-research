#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 src/run_experiment.py \
  --root . \
  --output experiments/smoke_1rep \
  --replications-per-scenario 1 \
  --policies B0 B3 B4 B6 \
  --allow-haversine-fallback \
  --overwrite
