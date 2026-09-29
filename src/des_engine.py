from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from datetime import timedelta
import math
import time
import numpy as np
import pandas as pd

from runtime_io import RuntimeConfig, load_sample_tables
from travel_provider import TravelProvider
from policies import select_candidate


SPECIALTY_EQUIV = {
    "general": {"general"},
    "trauma": {"trauma", "pediatric_trauma"},
    "stroke": {"stroke", "neurology"},
    "cardiology": {"cardiology", "pediatric_cardiology"},
    "obstetrics": {"obstetrics"},
    "pediatrics": {"pediatrics", "pediatric_trauma", "pediatric_cardiology"},
}


@dataclass
class VehicleState:
    vehicle_id: str
    lat: float
    lon: float
    node: int | None
    soc_pct: float
    battery_capacity_kwh: float
    consumption_kwh_km: float
    reserve_soc_pct: float
    status: str
    available_at: pd.Timestamp
    initial_status: str

    def refresh(self, now: pd.Timestamp):
        if now >= self.available_at:
            self.status = "available"

    def available(self, now: pd.Timestamp) -> bool:
        self.refresh(now)
        return self.status == "available" and now >= self.available_at


class CapacityModel:
    def __init__(self, df: pd.DataFrame, scenario_id: str, replication_id: str, hold_min: float):
        d = df[(df["scenario_id"] == scenario_id) & (df["replication_id"] == replication_id)].copy()
        d["timestamp_bin"] = pd.to_datetime(d["timestamp_bin"], utc=True)
        self.by_hospital = {}
        for h, g in d.groupby("hospital_id", sort=False):
            g = g.sort_values("timestamp_bin")
            self.by_hospital[h] = {
                "times": g["timestamp_bin"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[ns]"),
                "capacity": g["capacity_t"].to_numpy(int),
                "wait": g["estimated_wait_min"].to_numpy(float),
                "state": g["capacity_state"].astype(str).to_numpy(),
            }
        self.hold_min = float(hold_min)
        self.admissions: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]] = {}

    def exogenous_at(self, hospital_id: str, when: pd.Timestamp) -> tuple[int, float, str]:
        g = self.by_hospital[hospital_id]
        target = when.tz_convert("UTC").tz_localize(None).to_datetime64()
        idx = int(np.searchsorted(g["times"], target, side="right") - 1)
        idx = max(0, min(idx, len(g["times"])-1))
        return int(g["capacity"][idx]), float(g["wait"][idx]), str(g["state"][idx])

    def occupancy_at(self, hospital_id: str, when: pd.Timestamp) -> int:
        arr = self.admissions.get(hospital_id, [])
        return sum(1 for a, release in arr if a <= when < release)

    def runtime_at(self, hospital_id: str, when: pd.Timestamp) -> tuple[int, float, str, int, int]:
        exog, wait, state = self.exogenous_at(hospital_id, when)
        occ = self.occupancy_at(hospital_id, when)
        return max(0, exog - occ), wait, state, exog, occ

    def register_admission(self, hospital_id: str, arrival: pd.Timestamp):
        release = arrival + pd.Timedelta(minutes=self.hold_min)
        self.admissions.setdefault(hospital_id, []).append((arrival, release))


class SimulationEngine:
    def __init__(self, root: Path, scenario_id: str, replication_id: str, policy_id: str,
                 cfg: RuntimeConfig, allow_haversine_fallback: bool = False, tables=None, travel=None):
        self.root = Path(root)
        self.scenario_id = scenario_id
        self.replication_id = replication_id
        self.policy_id = policy_id.upper()
        self.cfg = cfg
        self.tables = tables if tables is not None else load_sample_tables(self.root)
        self.travel = travel if travel is not None else TravelProvider(self.root, cfg, allow_haversine_fallback=allow_haversine_fallback)
        self.events = []
        self.decisions = []
        self.missions = []
        self.violations = []
        self._event_seq = 0

        self.incidents = self.tables["incidents"]
        self.incidents = self.incidents[(self.incidents.scenario_id == scenario_id) & (self.incidents.replication_id == replication_id)].copy()
        self.incidents["timestamp"] = pd.to_datetime(self.incidents["timestamp"], utc=True)
        self.incidents = self.incidents.sort_values(["timestamp", "incident_id"]).reset_index(drop=True)

        rep_row = self.tables["replications"][(self.tables["replications"].scenario_id == scenario_id) & (self.tables["replications"].replication_id == replication_id)].iloc[0]
        self.random_seed = int(rep_row["random_seed"])
        self.common_random_group = str(rep_row["common_random_group"])

        self.hospitals = self.tables["hospitals"].copy()
        self.chargers = self.tables["chargers"].copy()
        self.hospital_records = self.hospitals.to_dict("records")
        self.charger_records = self.chargers.to_dict("records")
        self.hospital_support = {str(h["hospital_id"]): set(str(h["supported_specialties"]).split("|")) for h in self.hospital_records}
        self.hospital_nearest_charger = {}
        for h in self.hospital_records:
            best = None
            for c in self.charger_records:
                leg = self.travel.hospital_to_charger(h, c)
                if leg.reachable and (best is None or leg.travel_min < best[2].travel_min):
                    best = (c, c["charger_id"], leg)
            self.hospital_nearest_charger[str(h["hospital_id"])] = best
        self.capacity = CapacityModel(self.tables["capacity"], scenario_id, replication_id, cfg.hospital_occupancy_hold_min)

        f = self.tables["fleet"]
        f = f[(f.scenario_id == scenario_id) & (f.replication_id == replication_id)].copy()
        f["available_at_init"] = pd.to_datetime(f["available_at_init"], utc=True)
        self.vehicles: dict[str, VehicleState] = {}
        for _, r in f.iterrows():
            status = str(r["vehicle_state_init"])
            soc = float(r["soc_init_pct"])
            if status == "charging":
                # Explicit scaffold assumption: initial charging completes at configured target.
                soc = max(soc, float(cfg.initial_charging_target_pct))
            node = None
            if self.travel.mode == "osmnx":
                node = self.travel.entity_node("fleet_initial", r["vehicle_id"], scenario_id, replication_id)
            self.vehicles[r["vehicle_id"]] = VehicleState(
                str(r["vehicle_id"]), float(r["latitude"]), float(r["longitude"]), node,
                soc, float(r["battery_capacity_kwh"]), float(r["nominal_consumption_kwh_km"]),
                float(r["reserve_soc_pct"]), status, r["available_at_init"], status
            )

    def _log_event(self, when, event_type, **payload):
        self._event_seq += 1
        self.events.append({
            "scenario_id": self.scenario_id,
            "replication_id": self.replication_id,
            "policy_id": self.policy_id,
            "random_seed": self.random_seed,
            "sequence": self._event_seq,
            "timestamp": pd.Timestamp(when).isoformat(),
            "event_type": event_type,
            **payload,
        })

    def _hospital_supports(self, hospital_row, specialty: str) -> bool:
        supported = self.hospital_support[str(hospital_row["hospital_id"])]
        required = SPECIALTY_EQUIV.get(str(specialty), {str(specialty)})
        return bool(supported & required)

    def _nearest_charger(self, hospital_row):
        return self.hospital_nearest_charger.get(str(hospital_row["hospital_id"]))

    def _candidate_rows(self, incident: pd.Series) -> pd.DataFrame:
        now = incident["timestamp"]
        rows = []
        for v in self.vehicles.values():
            v.refresh(now)
            available = v.available(now)
            leg_vi = self.travel.vehicle_to_incident(v.__dict__, incident)
            if not leg_vi.reachable:
                # still enumerate hospitals for audit with unreachable flag
                pass
            for h in self.hospital_records:
                leg_ih = self.travel.incident_to_hospital(incident, h)
                route_reachable = leg_vi.reachable and leg_ih.reachable
                if route_reachable:
                    arrival = now + pd.Timedelta(minutes=leg_vi.travel_min + float(incident["service_time_scene_min"]) + leg_ih.travel_min)
                else:
                    arrival = now
                runtime_cap, wait_min, cap_state, exog_cap, occupancy = self.capacity.runtime_at(str(h["hospital_id"]), arrival)
                nearest_charger = self._nearest_charger(h)
                if nearest_charger is None:
                    charger_id = None
                    leg_hc = None
                    d_hc = np.inf
                    t_hc = np.inf
                    charger_power = np.nan
                    charger_lat = np.nan
                    charger_lon = np.nan
                    charger_node = None
                else:
                    c, charger_id, leg_hc = nearest_charger
                    d_hc = leg_hc.distance_km
                    t_hc = leg_hc.travel_min
                    charger_power = float(c["charger_power_kw"])
                    charger_lat = float(c["latitude"])
                    charger_lon = float(c["longitude"])
                    charger_node = self.travel.entity_node("charger", str(charger_id)) if self.travel.mode == "osmnx" else None

                cons = v.consumption_kwh_km
                e_vi = (leg_vi.distance_km * cons) if leg_vi.reachable else np.inf
                e_ih = (leg_ih.distance_km * cons) if leg_ih.reachable else np.inf
                e_hc = d_hc * cons if np.isfinite(d_hc) else np.inf
                e_equipment = float(incident["equipment_energy_kwh"])
                e_to_hospital = e_vi + e_ih + e_equipment
                e_det = e_to_hospital + e_hc
                e_unc = e_det * (1.0 + self.cfg.energy_uncertainty_buffer_pct/100.0)
                energy_available = v.battery_capacity_kwh * v.soc_pct/100.0
                reserve_kwh = v.battery_capacity_kwh * v.reserve_soc_pct/100.0
                physical_to_hospital_pass = bool(np.isfinite(e_to_hospital) and energy_available >= e_to_hospital)
                soc_pass_det = bool(np.isfinite(e_det) and energy_available - e_det >= reserve_kwh)
                soc_pass_unc = bool(np.isfinite(e_unc) and energy_available - e_unc >= reserve_kwh)
                soc_after_hospital = (energy_available - e_to_hospital)/v.battery_capacity_kwh*100.0 if np.isfinite(e_to_hospital) else np.nan
                soc_at_charger = (energy_available - e_det)/v.battery_capacity_kwh*100.0 if np.isfinite(e_det) else np.nan
                cap_pass = runtime_cap > 0
                capability_pass = self._hospital_supports(h, str(incident["required_specialty"]))
                emergency_pass = int(h["emergency_receiving_flag"]) == 1
                sla_pass = bool(route_reachable and leg_vi.travel_min <= float(incident["max_response_time_min"]))
                usable_kwh = max(1e-9, energy_available - reserve_kwh)
                energy_fraction_det = e_det / usable_kwh if np.isfinite(e_det) else np.inf
                energy_fraction_unc = e_unc / usable_kwh if np.isfinite(e_unc) else np.inf

                rows.append({
                    "candidate_id": f"{incident['incident_id']}__{v.vehicle_id}__{h['hospital_id']}",
                    "vehicle_id": v.vehicle_id,
                    "hospital_id": str(h["hospital_id"]),
                    "availability_pass": bool(available),
                    "route_reachable": bool(route_reachable),
                    "emergency_receiving_pass": bool(emergency_pass),
                    "capability_pass": bool(capability_pass),
                    "capacity_pass": bool(cap_pass),
                    "sla_pass": bool(sla_pass),
                    "physical_energy_to_hospital_pass": physical_to_hospital_pass,
                    "soc_pass_deterministic": soc_pass_det,
                    "soc_pass_uncertainty": soc_pass_unc,
                    "response_time_min": float(leg_vi.travel_min) if leg_vi.reachable else np.inf,
                    "transport_time_min": float(leg_ih.travel_min) if leg_ih.reachable else np.inf,
                    "hospital_wait_min": float(wait_min),
                    "transport_plus_wait_min": (float(leg_ih.travel_min) + float(wait_min)) if leg_ih.reachable and wait_min < 900 else np.inf,
                    "distance_vehicle_incident_km": float(leg_vi.distance_km) if leg_vi.reachable else np.nan,
                    "distance_incident_hospital_km": float(leg_ih.distance_km) if leg_ih.reachable else np.nan,
                    "distance_hospital_charger_km": float(d_hc) if np.isfinite(d_hc) else np.nan,
                    "energy_to_hospital_kwh": float(e_to_hospital) if np.isfinite(e_to_hospital) else np.nan,
                    "energy_required_deterministic_kwh": float(e_det) if np.isfinite(e_det) else np.nan,
                    "energy_required_uncertainty_kwh": float(e_unc) if np.isfinite(e_unc) else np.nan,
                    "energy_fraction_deterministic": float(energy_fraction_det),
                    "energy_fraction_uncertainty": float(energy_fraction_unc),
                    "soc_before_pct": float(v.soc_pct),
                    "soc_after_hospital_pct": float(soc_after_hospital) if np.isfinite(soc_after_hospital) else np.nan,
                    "soc_at_charger_pct": float(soc_at_charger) if np.isfinite(soc_at_charger) else np.nan,
                    "reserve_soc_pct": float(v.reserve_soc_pct),
                    "capacity_exogenous": int(exog_cap),
                    "capacity_policy_occupancy": int(occupancy),
                    "capacity_runtime": int(runtime_cap),
                    "capacity_state": cap_state,
                    "expected_hospital_arrival": arrival,
                    "charger_id": charger_id,
                    "charger_power_kw": charger_power,
                    "charger_travel_min": float(t_hc) if np.isfinite(t_hc) else np.nan,
                    "charger_lat": charger_lat,
                    "charger_lon": charger_lon,
                    "charger_node": charger_node,
                    "hospital_lat": float(h["latitude"]),
                    "hospital_lon": float(h["longitude"]),
                    "hospital_node": self.travel.entity_node("hospital", str(h["hospital_id"])) if self.travel.mode == "osmnx" else None,
                    "routing_source": self.travel.mode,
                })
        return pd.DataFrame(rows)

    def _register_violation(self, incident_id, kind, selected):
        self.violations.append({
            "scenario_id": self.scenario_id,
            "replication_id": self.replication_id,
            "policy_id": self.policy_id,
            "incident_id": incident_id,
            "violation_type": kind,
            "candidate_id": selected.get("candidate_id") if selected is not None else None,
        })

    def _apply_mission(self, incident, selected, decision):
        now = incident["timestamp"]
        v = self.vehicles[selected["vehicle_id"]]
        response = float(selected["response_time_min"])
        scene = float(incident["service_time_scene_min"])
        transport = float(selected["transport_time_min"])
        wait = float(selected["hospital_wait_min"])
        wait_effective = 0.0 if wait >= 900 else wait
        t_scene = now + pd.Timedelta(minutes=response)
        t_transport = t_scene + pd.Timedelta(minutes=scene)
        t_hospital = t_transport + pd.Timedelta(minutes=transport)
        t_handover = t_hospital + pd.Timedelta(minutes=wait_effective + self.cfg.handover_service_min)

        self._log_event(now, "IncidentCreated", incident_id=incident["incident_id"], priority=incident["priority"])
        self._log_event(now, "AmbulanceDispatched", incident_id=incident["incident_id"], vehicle_id=v.vehicle_id, hospital_id=selected["hospital_id"])
        self._log_event(t_scene, "AmbulanceArrivesPatient", incident_id=incident["incident_id"], vehicle_id=v.vehicle_id)
        self._log_event(t_transport, "TransportStarts", incident_id=incident["incident_id"], vehicle_id=v.vehicle_id, hospital_id=selected["hospital_id"])
        self._log_event(t_hospital, "HospitalArrival", incident_id=incident["incident_id"], vehicle_id=v.vehicle_id, hospital_id=selected["hospital_id"])
        self._log_event(t_handover, "HandoverComplete", incident_id=incident["incident_id"], vehicle_id=v.vehicle_id, hospital_id=selected["hospital_id"])

        # Hospital runtime occupancy is policy-specific synthetic state.
        self.capacity.register_admission(selected["hospital_id"], t_hospital)

        energy_to_hospital = float(selected["energy_to_hospital_kwh"])
        energy_after_hospital = v.battery_capacity_kwh * v.soc_pct/100.0 - energy_to_hospital
        soc_after_hospital = max(0.0, min(100.0, energy_after_hospital/v.battery_capacity_kwh*100.0))

        final_time = t_handover
        final_soc = soc_after_hospital
        final_lat = float(selected["hospital_lat"])
        final_lon = float(selected["hospital_lon"])
        final_node = selected["hospital_node"]
        charged = False

        # Charging decision is separate from dispatch feasibility and is an explicit scaffold assumption.
        if soc_after_hospital < max(float(self.cfg.charge_trigger_pct), float(v.reserve_soc_pct)) and selected.get("charger_id"):
            e_hc = max(0.0, float(selected["energy_required_deterministic_kwh"]) - float(selected["energy_to_hospital_kwh"]))
            soc_at_charger = max(0.0, (energy_after_hospital - e_hc)/v.battery_capacity_kwh*100.0)
            t_charge_start = t_handover + pd.Timedelta(minutes=float(selected["charger_travel_min"]))
            target = max(float(self.cfg.charge_target_pct), float(v.reserve_soc_pct))
            kwh_needed = max(0.0, v.battery_capacity_kwh*(target - soc_at_charger)/100.0)
            charge_min = kwh_needed / max(1e-9, float(selected["charger_power_kw"])) * 60.0
            t_charge_end = t_charge_start + pd.Timedelta(minutes=charge_min)
            self._log_event(t_charge_start, "ChargingStarts", incident_id=incident["incident_id"], vehicle_id=v.vehicle_id, charger_id=selected["charger_id"], soc_before_pct=round(soc_at_charger,3))
            self._log_event(t_charge_end, "ChargingEnds", incident_id=incident["incident_id"], vehicle_id=v.vehicle_id, charger_id=selected["charger_id"], soc_after_pct=round(target,3))
            final_time = t_charge_end
            final_soc = target
            final_lat = float(selected["charger_lat"])
            final_lon = float(selected["charger_lon"])
            final_node = selected["charger_node"]
            charged = True

        self._log_event(final_time, "AmbulanceAvailable", incident_id=incident["incident_id"], vehicle_id=v.vehicle_id, soc_pct=round(final_soc,3))
        v.available_at = final_time
        v.status = "busy"
        v.soc_pct = final_soc
        v.lat, v.lon, v.node = final_lat, final_lon, final_node

        # Violations are evaluated on the selected assignment regardless of policy awareness.
        if not bool(selected["capability_pass"]): self._register_violation(incident["incident_id"], "hospital_capability", selected)
        if not bool(selected["capacity_pass"]): self._register_violation(incident["incident_id"], "hospital_capacity", selected)
        if not bool(selected["soc_pass_deterministic"]): self._register_violation(incident["incident_id"], "soc_reserve_deterministic", selected)
        if not bool(selected["sla_pass"]): self._register_violation(incident["incident_id"], "response_sla", selected)

        all_hard = bool(selected["capability_pass"] and selected["capacity_pass"] and selected["soc_pass_deterministic"] and selected["availability_pass"])
        time_to_hospital = response + scene + transport
        self.missions.append({
            "scenario_id": self.scenario_id,
            "replication_id": self.replication_id,
            "policy_id": self.policy_id,
            "random_seed": self.random_seed,
            "incident_id": incident["incident_id"],
            "priority": incident["priority"],
            "vehicle_id": selected["vehicle_id"],
            "hospital_id": selected["hospital_id"],
            "served": 1,
            "overall_feasible": int(all_hard),
            "sla_pass": int(bool(selected["sla_pass"])),
            "capability_pass": int(bool(selected["capability_pass"])),
            "capacity_pass": int(bool(selected["capacity_pass"])),
            "soc_pass": int(bool(selected["soc_pass_deterministic"])),
            "response_time_min": response,
            "transport_time_min": transport,
            "hospital_wait_min": wait_effective,
            "time_to_compatible_hospital_min": time_to_hospital if bool(selected["capability_pass"]) else np.nan,
            "soc_before_pct": float(selected["soc_before_pct"]),
            "soc_after_hospital_pct": soc_after_hospital,
            "soc_final_pct": final_soc,
            "charged_after_mission": int(charged),
            "solver_time_sec": float(decision.solver_time_sec),
            "mip_gap": decision.mip_gap,
            "epsilon_relaxed": int(decision.epsilon_relaxed),
            "routing_source": selected["routing_source"],
        })

    def run(self) -> dict[str, pd.DataFrame | dict]:
        for _, incident in self.incidents.iterrows():
            candidates = self._candidate_rows(incident)
            decision = select_candidate(self.policy_id, candidates, incident, self.cfg)
            selected = None if decision.selected_index is None else candidates.loc[decision.selected_index].to_dict()
            self.decisions.append({
                "scenario_id": self.scenario_id,
                "replication_id": self.replication_id,
                "policy_id": self.policy_id,
                "random_seed": self.random_seed,
                "common_random_group": self.common_random_group,
                "incident_id": incident["incident_id"],
                "priority": incident["priority"],
                "decision_status": decision.status,
                "selected_candidate_id": selected.get("candidate_id") if selected else None,
                "selected_vehicle_id": selected.get("vehicle_id") if selected else None,
                "selected_hospital_id": selected.get("hospital_id") if selected else None,
                "candidate_count": decision.candidate_count,
                "feasible_candidate_count": decision.feasible_candidate_count,
                "solver_time_sec": decision.solver_time_sec,
                "mip_gap": decision.mip_gap,
                "epsilon_transport": decision.epsilon_transport,
                "epsilon_energy_fraction": decision.epsilon_energy_fraction,
                "epsilon_relaxed": int(decision.epsilon_relaxed),
                "routing_source": self.travel.mode,
            })
            if selected is None:
                self._log_event(incident["timestamp"], "IncidentUnserved", incident_id=incident["incident_id"], priority=incident["priority"], reason=decision.status)
                self.missions.append({
                    "scenario_id": self.scenario_id,
                    "replication_id": self.replication_id,
                    "policy_id": self.policy_id,
                    "random_seed": self.random_seed,
                    "incident_id": incident["incident_id"],
                    "priority": incident["priority"],
                    "vehicle_id": None,
                    "hospital_id": None,
                    "served": 0,
                    "overall_feasible": 0,
                    "sla_pass": 0,
                    "capability_pass": 0,
                    "capacity_pass": 0,
                    "soc_pass": 0,
                    "response_time_min": np.nan,
                    "transport_time_min": np.nan,
                    "hospital_wait_min": np.nan,
                    "time_to_compatible_hospital_min": np.nan,
                    "soc_before_pct": np.nan,
                    "soc_after_hospital_pct": np.nan,
                    "soc_final_pct": np.nan,
                    "charged_after_mission": 0,
                    "solver_time_sec": float(decision.solver_time_sec),
                    "mip_gap": decision.mip_gap,
                    "epsilon_relaxed": int(decision.epsilon_relaxed),
                    "routing_source": self.travel.mode,
                })
                continue
            self._apply_mission(incident, selected, decision)

        missions = pd.DataFrame(self.missions)
        decisions = pd.DataFrame(self.decisions)
        events = pd.DataFrame(self.events).sort_values(["timestamp", "sequence"]) if self.events else pd.DataFrame()
        violations = pd.DataFrame(self.violations)
        metrics = self._metrics(missions, decisions, violations)
        return {"missions": missions, "decisions": decisions, "events": events, "violations": violations, "metrics": metrics}

    def _metrics(self, m: pd.DataFrame, decisions: pd.DataFrame, violations: pd.DataFrame) -> dict:
        total = len(m)
        served = int(m["served"].sum()) if total else 0
        metric = {
            "scenario_id": self.scenario_id,
            "replication_id": self.replication_id,
            "policy_id": self.policy_id,
            "random_seed": self.random_seed,
            "common_random_group": self.common_random_group,
            "incident_count": total,
            "served_count": served,
            "unserved_count": total-served,
            "served_rate": served/total if total else np.nan,
            "feasible_rate_overall": float(m["overall_feasible"].mean()) if total else np.nan,
            "SLA_pass_rate": float(m["sla_pass"].mean()) if total else np.nan,
            "SOC_violation_count": int(((m["served"] == 1) & (m["soc_pass"] == 0)).sum()) if total else 0,
            "hospital_capacity_violations": int(((m["served"] == 1) & (m["capacity_pass"] == 0)).sum()) if total else 0,
            "hospital_capability_violations": int(((m["served"] == 1) & (m["capability_pass"] == 0)).sum()) if total else 0,
            "assignments_to_charging": 0,
            "avg_response_time_min": float(m.loc[m.served==1, "response_time_min"].mean()) if served else np.nan,
            "P95_response_P1": float(m.loc[(m.served==1)&(m.priority=="P1"), "response_time_min"].quantile(.95)) if ((m.served==1)&(m.priority=="P1")).any() else np.nan,
            "time_to_compatible_hospital": float(m.loc[(m.served==1)&(m.capability_pass==1), "time_to_compatible_hospital_min"].mean()) if ((m.served==1)&(m.capability_pass==1)).any() else np.nan,
            "charged_after_mission_count": int(m["charged_after_mission"].sum()) if total else 0,
            "avg_solver_time_sec": float(decisions["solver_time_sec"].mean()) if len(decisions) else 0.0,
            "max_mip_gap": float(pd.to_numeric(decisions["mip_gap"], errors="coerce").max()) if "mip_gap" in decisions and pd.to_numeric(decisions["mip_gap"], errors="coerce").notna().any() else np.nan,
            "b6_epsilon_relaxation_count": int(decisions["epsilon_relaxed"].sum()) if "epsilon_relaxed" in decisions else 0,
            "routing_source": self.travel.mode,
        }
        for p in ["P1","P2","P3","P4"]:
            q = m[m.priority == p]
            metric[f"feasible_rate_{p}"] = float(q["overall_feasible"].mean()) if len(q) else np.nan
        return metric


def run_replication(root: Path, scenario_id: str, replication_id: str, policy_id: str,
                    cfg: RuntimeConfig, allow_haversine_fallback: bool = False, tables=None, travel=None):
    return SimulationEngine(root, scenario_id, replication_id, policy_id, cfg, allow_haversine_fallback, tables=tables, travel=travel).run()
