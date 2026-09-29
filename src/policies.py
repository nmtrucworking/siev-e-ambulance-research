from __future__ import annotations

from dataclasses import dataclass
import time
import numpy as np
import pandas as pd


@dataclass
class PolicyDecision:
    selected_index: int | None
    status: str
    solver_time_sec: float = 0.0
    mip_gap: float | None = None
    epsilon_transport: float | None = None
    epsilon_energy_fraction: float | None = None
    epsilon_relaxed: bool = False
    candidate_count: int = 0
    feasible_candidate_count: int = 0


def _choose_lexicographic(df: pd.DataFrame, cols: list[str]) -> int | None:
    if df.empty:
        return None
    return int(df.sort_values(cols, kind="mergesort").index[0])


def select_b0(candidates: pd.DataFrame, incident, cfg) -> PolicyDecision:
    # B0: nearest available ambulance + nearest basic receiving hospital.
    # It deliberately ignores specialty/capacity/reserve-SOC, but still requires
    # route reachability and enough raw energy to physically reach hospital.
    q = candidates[
        candidates["availability_pass"] &
        candidates["route_reachable"] &
        candidates["emergency_receiving_pass"] &
        candidates["physical_energy_to_hospital_pass"]
    ]
    idx = _choose_lexicographic(q, ["response_time_min", "transport_time_min", "hospital_id"])
    return PolicyDecision(
        selected_index=idx,
        status="SELECTED" if idx is not None else "NO_FEASIBLE_CANDIDATE",
        candidate_count=len(candidates), feasible_candidate_count=len(q),
    )


def select_b3(candidates: pd.DataFrame, incident, cfg) -> PolicyDecision:
    q = candidates[
        candidates["availability_pass"] &
        candidates["route_reachable"] &
        candidates["emergency_receiving_pass"] &
        candidates["physical_energy_to_hospital_pass"] &
        candidates["capability_pass"] &
        candidates["capacity_pass"]
    ]
    idx = _choose_lexicographic(q, ["response_time_min", "transport_plus_wait_min", "hospital_id"])
    return PolicyDecision(
        selected_index=idx,
        status="SELECTED" if idx is not None else "NO_FEASIBLE_CANDIDATE",
        candidate_count=len(candidates), feasible_candidate_count=len(q),
    )


def select_b4(candidates: pd.DataFrame, incident, cfg) -> PolicyDecision:
    q = candidates[
        candidates["availability_pass"] &
        candidates["route_reachable"] &
        candidates["emergency_receiving_pass"] &
        candidates["capability_pass"] &
        candidates["capacity_pass"] &
        candidates["soc_pass_deterministic"]
    ]
    idx = _choose_lexicographic(q, ["response_time_min", "transport_plus_wait_min", "energy_fraction_deterministic"])
    return PolicyDecision(
        selected_index=idx,
        status="SELECTED" if idx is not None else "NO_FEASIBLE_CANDIDATE",
        candidate_count=len(candidates), feasible_candidate_count=len(q),
    )


def select_b6(candidates: pd.DataFrame, incident, cfg) -> PolicyDecision:
    q = candidates[
        candidates["availability_pass"] &
        candidates["route_reachable"] &
        candidates["emergency_receiving_pass"] &
        candidates["capability_pass"] &
        candidates["capacity_pass"] &
        candidates["soc_pass_uncertainty"]
    ].copy()
    if q.empty:
        return PolicyDecision(None, "NO_FEASIBLE_CANDIDATE", candidate_count=len(candidates), feasible_candidate_count=0)

    # Pre-specified operational epsilon rule. This is one operating point, not a full Pareto frontier.
    min_transport = float(q["transport_plus_wait_min"].min())
    eps_transport = min_transport + float(cfg.transport_slack(str(incident["priority"])))
    min_energy = float(q["energy_fraction_uncertainty"].min())
    eps_energy = min(1.0, min_energy + float(cfg.b6_energy_fraction_slack))

    # Primary objective combines response and urgency-weighted SLA lateness.
    sla = float(incident["max_response_time_min"])
    urgency = float(incident["urgency_weight"])
    lateness = np.maximum(0.0, q["response_time_min"].to_numpy(float) - sla)
    objective = q["response_time_min"].to_numpy(float) + urgency * lateness

    t0 = time.perf_counter()
    try:
        from scipy.optimize import milp, LinearConstraint, Bounds
        n = len(q)
        constraints = [LinearConstraint(np.ones((1, n)), lb=[1.0], ub=[1.0])]
        constraints.append(LinearConstraint(
            q["transport_plus_wait_min"].to_numpy(float)[None, :],
            lb=[-np.inf], ub=[eps_transport]
        ))
        constraints.append(LinearConstraint(
            q["energy_fraction_uncertainty"].to_numpy(float)[None, :],
            lb=[-np.inf], ub=[eps_energy]
        ))
        res = milp(
            c=objective,
            integrality=np.ones(n),
            bounds=Bounds(np.zeros(n), np.ones(n)),
            constraints=constraints,
            options={"time_limit": 10.0},
        )
        elapsed = time.perf_counter() - t0
        if res.success and res.x is not None:
            pos = int(np.argmax(res.x))
            idx = int(q.index[pos])
            return PolicyDecision(
                idx, "SELECTED", elapsed,
                float(getattr(res, "mip_gap", np.nan)) if getattr(res, "mip_gap", None) is not None else None,
                eps_transport, eps_energy, False, len(candidates), len(q)
            )
    except Exception:
        elapsed = time.perf_counter() - t0

    # Emergency fallback: relax epsilon but keep hard feasibility. This is logged.
    idx = _choose_lexicographic(q.assign(_primary=objective), ["_primary", "transport_plus_wait_min", "energy_fraction_uncertainty"])
    return PolicyDecision(
        idx, "SELECTED_EPSILON_RELAXED", elapsed, None,
        eps_transport, eps_energy, True, len(candidates), len(q)
    )


def select_candidate(policy_id: str, candidates: pd.DataFrame, incident, cfg) -> PolicyDecision:
    policy_id = policy_id.upper()
    if policy_id == "B0":
        return select_b0(candidates, incident, cfg)
    if policy_id == "B3":
        return select_b3(candidates, incident, cfg)
    if policy_id == "B4":
        return select_b4(candidates, incident, cfg)
    if policy_id == "B6":
        return select_b6(candidates, incident, cfg)
    raise ValueError(f"Unknown policy_id={policy_id}")
