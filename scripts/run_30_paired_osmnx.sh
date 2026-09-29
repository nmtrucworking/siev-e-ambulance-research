#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 src/run_experiment.py \
  --root . \
  --output experiments/paired_30 \
  --replications-per-scenario 30 \
  --policies B0 B3 B4 B6 \
  --overwrite
python3 src/validate_runtime_outputs.py --experiment experiments/paired_30
python3 src/analyze_paired.py \
  --input experiments/paired_30/replication_metrics.csv \
  --output experiments/paired_30/analysis
