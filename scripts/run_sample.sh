#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python3 "$ROOT/src/generate_dataset.py" --output "$ROOT"
python3 "$ROOT/src/validate_dataset.py" --root "$ROOT"
