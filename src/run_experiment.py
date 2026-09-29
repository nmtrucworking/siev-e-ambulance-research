#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import json
import sys
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from runtime_io import load_runtime_config, load_sample_tables
from des_engine import run_replication
from travel_provider import TravelProvider


def write_frame(df: pd.DataFrame, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        df.to_csv(path, index=False, compression="gzip")
    else:
        df.to_csv(path, index=False)


def main():
    ap = argparse.ArgumentParser(description="Run rolling DES policies for E-Ambulance synthetic benchmark")
    ap.add_argument("--root", default=".")
    ap.add_argument("--config", default="configs/runtime_model.json")
    ap.add_argument("--output", default="experiments/paired_30")
    ap.add_argument("--scenarios", nargs="*", default=None)
    ap.add_argument("--policies", nargs="*", default=["B0","B3","B4","B6"])
    ap.add_argument("--replications-per-scenario", type=int, default=30)
    ap.add_argument("--allow-haversine-fallback", action="store_true", help="SMOKE TEST ONLY; final experiment should use OSMnx artifacts")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    cfg_path = Path(args.config)
    if not cfg_path.is_absolute():
        cfg_path = root / cfg_path
    cfg = load_runtime_config(cfg_path)
    tables = load_sample_tables(root)
    travel = TravelProvider(root, cfg, allow_haversine_fallback=args.allow_haversine_fallback)
    reps = tables["replications"].copy()
    scenarios = args.scenarios or list(tables["scenarios"]["scenario_id"].astype(str))
    policies = [p.upper() for p in args.policies]

    out = Path(args.output)
    if not out.is_absolute():
        out = root / out
    out.mkdir(parents=True, exist_ok=True)

    selected_reps = []
    for s in scenarios:
        sub = reps[reps.scenario_id == s].sort_values("replication_id").head(args.replications_per_scenario)
        selected_reps.extend(sub.to_dict("records"))

    manifest_rows = []
    for r in selected_reps:
        for p in policies:
            manifest_rows.append({
                "scenario_id": r["scenario_id"],
                "replication_id": r["replication_id"],
                "random_seed": int(r["random_seed"]),
                "common_random_group": r["common_random_group"],
                "policy_id": p,
            })
    pd.DataFrame(manifest_rows).to_csv(out/"run_manifest.csv", index=False)

    metrics_rows = []
    failures = []
    for i, row in enumerate(manifest_rows, 1):
        s, rep, p = row["scenario_id"], row["replication_id"], row["policy_id"]
        run_dir = out / "runs" / s / rep / p
        metric_path = run_dir / "replication_metrics.json"
        if metric_path.exists() and not args.overwrite:
            metrics_rows.append(json.loads(metric_path.read_text(encoding="utf-8")))
            print(f"[{i}/{len(manifest_rows)}] skip existing {s}/{rep}/{p}")
            continue
        print(f"[{i}/{len(manifest_rows)}] run {s}/{rep}/{p}")
        try:
            result = run_replication(root, s, rep, p, cfg, allow_haversine_fallback=args.allow_haversine_fallback, tables=tables, travel=travel)
            run_dir.mkdir(parents=True, exist_ok=True)
            write_frame(result["decisions"], run_dir/"dispatch_decisions.csv.gz")
            write_frame(result["events"], run_dir/"des_event_log.csv.gz")
            write_frame(result["missions"], run_dir/"mission_metrics.csv.gz")
            write_frame(result["violations"], run_dir/"constraint_violations.csv")
            metric_path.write_text(json.dumps(result["metrics"], indent=2, default=str), encoding="utf-8")
            metrics_rows.append(result["metrics"])
        except Exception as e:
            failures.append({**row, "error": repr(e)})
            print("  FAILED:", repr(e))

    metrics = pd.DataFrame(metrics_rows)
    metrics.to_csv(out/"replication_metrics.csv", index=False)
    pd.DataFrame(failures).to_csv(out/"run_failures.csv", index=False)

    summary = {
        "scenarios": scenarios,
        "policies": policies,
        "replications_per_scenario_requested": args.replications_per_scenario,
        "expected_policy_runs": len(manifest_rows),
        "completed_policy_runs": len(metrics_rows),
        "failed_policy_runs": len(failures),
        "allow_haversine_fallback": bool(args.allow_haversine_fallback),
        "final_evidence_ready": bool(len(failures)==0 and not args.allow_haversine_fallback),
        "warning": None if not args.allow_haversine_fallback else "Haversine fallback is smoke-test routing only; results are not final OSMnx network evidence."
    }
    (out/"run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
