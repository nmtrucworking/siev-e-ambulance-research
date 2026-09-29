from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import json
import pandas as pd


@dataclass
class RuntimeConfig:
    energy_uncertainty_buffer_pct: float = 15.0
    handover_service_min: float = 10.0
    hospital_occupancy_hold_min: float = 30.0
    charge_trigger_pct: float = 35.0
    charge_target_pct: float = 80.0
    initial_charging_target_pct: float = 80.0
    b6_transport_slack_min_p1: float = 6.0
    b6_transport_slack_min_p2: float = 10.0
    b6_transport_slack_min_p3: float = 15.0
    b6_transport_slack_min_p4: float = 20.0
    b6_energy_fraction_slack: float = 0.20
    haversine_speed_vehicle_incident_kph: float = 35.0
    haversine_speed_incident_hospital_kph: float = 32.0
    haversine_speed_hospital_charger_kph: float = 35.0
    require_osm_for_final: bool = True

    def transport_slack(self, priority: str) -> float:
        return {
            "P1": self.b6_transport_slack_min_p1,
            "P2": self.b6_transport_slack_min_p2,
            "P3": self.b6_transport_slack_min_p3,
            "P4": self.b6_transport_slack_min_p4,
        }.get(priority, self.b6_transport_slack_min_p3)

    def to_dict(self) -> dict:
        return asdict(self)


def load_runtime_config(path: Path | None) -> RuntimeConfig:
    cfg = RuntimeConfig()
    if path is None or not Path(path).exists():
        return cfg
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    known = cfg.to_dict()
    for k, v in data.items():
        if k in known:
            setattr(cfg, k, v)
    return cfg


def save_runtime_config(path: Path, cfg: RuntimeConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg.to_dict(), indent=2), encoding="utf-8")


def load_sample_tables(root: Path) -> dict[str, pd.DataFrame]:
    sample = root / "data" / "sample"
    return {
        "incidents": pd.read_csv(sample / "incidents.csv.gz"),
        "fleet": pd.read_csv(sample / "fleet_initial.csv.gz"),
        "hospitals": pd.read_csv(sample / "hospitals.csv"),
        "capacity": pd.read_csv(sample / "hospital_capacity_timeseries.csv.gz"),
        "chargers": pd.read_csv(sample / "charging_stations.csv"),
        "replications": pd.read_csv(sample / "replications.csv.gz"),
        "scenarios": pd.read_csv(sample / "scenarios.csv.gz"),
    }
