#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 src/run_experiment.py \
  --root . \
  --output experiments/smoke_3rep_local \
  --replications-per-scenario 3 \
  --policies B0 B3 B4 B6 \
  --allow-haversine-fallback \
  --overwrite
python3 src/validate_runtime_outputs.py --experiment experiments/smoke_3rep_local
python3 src/analyze_paired.py \
  --input experiments/smoke_3rep_local/replication_metrics.csv \
  --output experiments/smoke_3rep_local/analysis
