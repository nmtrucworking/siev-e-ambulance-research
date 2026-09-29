#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import json
import numpy as np
import pandas as pd

HIGHER_BETTER = {
    "served_rate", "feasible_rate_overall", "SLA_pass_rate",
    "feasible_rate_P1", "feasible_rate_P2", "feasible_rate_P3", "feasible_rate_P4",
}
LOWER_BETTER = {
    "avg_response_time_min", "P95_response_P1", "time_to_compatible_hospital",
    "SOC_violation_count", "hospital_capacity_violations", "hospital_capability_violations",
    "unserved_count", "avg_solver_time_sec", "max_mip_gap",
}


def bootstrap_ci(x, n_boot=5000, seed=20260929):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(x), size=(n_boot, len(x)))
    means = x[idx].mean(axis=1)
    return float(np.quantile(means, .025)), float(np.quantile(means, .975))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="experiments/paired_30/replication_metrics.csv")
    ap.add_argument("--output", default="experiments/paired_30/analysis")
    ap.add_argument("--reference", default="B6")
    args = ap.parse_args()

    inp = Path(args.input)
    df = pd.read_csv(inp)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    required_policies = {"B0","B3","B4","B6"}
    audit = (
        df.groupby(["scenario_id","replication_id"])
        .agg(policy_count=("policy_id","nunique"), seed_count=("random_seed","nunique"), group_count=("common_random_group","nunique"))
        .reset_index()
    )
    audit["complete_pair"] = (audit.policy_count==4) & (audit.seed_count==1) & (audit.group_count==1)
    audit.to_csv(out/"pairing_audit.csv", index=False)

    completeness = {
        "pairs_total_present": int(len(audit)),
        "complete_pairs": int(audit.complete_pair.sum()),
        "all_pairs_complete": bool(audit.complete_pair.all() and len(audit)>0),
        "scenario_replication_counts": audit.groupby("scenario_id")["replication_id"].nunique().to_dict(),
    }
    (out/"pairing_completeness.json").write_text(json.dumps(completeness, indent=2), encoding="utf-8")

    metrics = [m for m in sorted(HIGHER_BETTER | LOWER_BETTER) if m in df.columns]
    rows = []
    for scenario, sdf in df.groupby("scenario_id"):
        wide = sdf.pivot(index="replication_id", columns="policy_id")
        for base in ["B0","B3","B4"]:
            for metric in metrics:
                try:
                    ref = wide[(metric, args.reference)]
                    cmp = wide[(metric, base)]
                except KeyError:
                    continue
                paired = pd.concat([ref, cmp], axis=1, keys=["ref","base"]).dropna()
                if paired.empty:
                    continue
                # Signed effect oriented so positive means B6 better.
                raw_diff = paired["ref"] - paired["base"]
                oriented = raw_diff if metric in HIGHER_BETTER else -raw_diff
                lo, hi = bootstrap_ci(oriented.to_numpy())
                sd = float(oriented.std(ddof=1)) if len(oriented) > 1 else np.nan
                dz = float(oriented.mean()/sd) if np.isfinite(sd) and sd > 0 else np.nan
                rows.append({
                    "scenario_id": scenario,
                    "comparison": f"{args.reference}-{base}",
                    "metric": metric,
                    "n_pairs": len(oriented),
                    "mean_oriented_improvement": float(oriented.mean()),
                    "median_oriented_improvement": float(oriented.median()),
                    "bootstrap95_low": lo,
                    "bootstrap95_high": hi,
                    "paired_effect_size_dz": dz,
                    "positive_means": f"{args.reference} better",
                })
    result = pd.DataFrame(rows)
    result.to_csv(out/"paired_comparison_summary.csv", index=False)
    print(json.dumps(completeness, indent=2))
    print("Wrote:", out/"paired_comparison_summary.csv")


if __name__ == "__main__":
    main()
