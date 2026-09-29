from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import numpy as np
import pandas as pd


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = math.radians(float(lat2) - float(lat1))
    dl = math.radians(float(lon2) - float(lon1))
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))


@dataclass
class TravelLeg:
    distance_km: float
    freeflow_min: float
    travel_min: float
    source: str
    reachable: bool = True


class TravelProvider:
    """Routing provider.

    Priority:
    1) OSMnx cache / graph if Notebook 02 outputs exist.
    2) Explicitly-labeled Haversine fallback for smoke testing only.

    The fallback is not valid as final network-routing evidence.
    """

    def __init__(self, root: Path, cfg, allow_haversine_fallback: bool = False):
        self.root = Path(root)
        self.cfg = cfg
        self.allow_fallback = allow_haversine_fallback
        self.osm_dir = self.root / "data" / "osmnx"
        self.routing_dir = self.root / "data" / "routing"
        self.mode = None
        self.G = None
        self.ox = None
        self.point_map = None
        self.ih = None
        self.hc = None
        self.cache = {}

        graph = self.osm_dir / "study_graph.graphml"
        point_map = self.osm_dir / "point_node_map.csv"
        ih = self.osm_dir / "incident_hospital_od.csv.gz"
        hc = self.osm_dir / "hospital_charger_od.csv"
        if graph.exists() and point_map.exists() and ih.exists() and hc.exists():
            try:
                import osmnx as ox
                self.ox = ox
                self.G = ox.io.load_graphml(graph)
                self.point_map = pd.read_csv(point_map)
                self.ih = pd.read_csv(ih)
                self.hc = pd.read_csv(hc)
                self.mode = "osmnx"
            except Exception as e:
                if not allow_haversine_fallback:
                    raise RuntimeError(f"OSMnx files exist but could not be loaded: {e}") from e

        if self.mode is None:
            if not allow_haversine_fallback:
                raise FileNotFoundError(
                    "OSMnx routing artifacts are missing. Run Notebook 02 with RUN_NETWORK=True, "
                    "or use --allow-haversine-fallback for smoke testing only."
                )
            self.mode = "haversine_smoke_test"

    def _gamma(self, scenario_id: str, timestamp) -> float:
        # If Notebook 03 has a calibrated profile, callers may replace this layer.
        # Until then, OSM mode is free-flow and fallback uses generator-aligned
        # deterministic scenario multipliers only for smoke testing.
        if self.mode == "osmnx":
            return 1.0
        return {"normal": 1.08, "peak": 1.28, "high_demand": 1.22}.get(scenario_id, 1.0)

    def entity_node(self, point_type: str, entity_id: str, scenario_id=None, replication_id=None):
        pm = self.point_map
        q = pm[(pm["point_type"] == point_type) & (pm["entity_id"] == entity_id)]
        if scenario_id is not None and "scenario_id" in q:
            q = q[q["scenario_id"].astype(str) == str(scenario_id)]
        if replication_id is not None and "replication_id" in q:
            q = q[q["replication_id"].astype(str) == str(replication_id)]
        if q.empty:
            return None
        return int(q.iloc[0]["osmid"])

    def vehicle_to_incident(self, vehicle, incident) -> TravelLeg:
        gamma = self._gamma(incident["scenario_id"], incident["timestamp"])
        if self.mode == "osmnx":
            origin = vehicle.get("node")
            if origin is None:
                origin = self.entity_node(
                    "fleet_initial", vehicle["vehicle_id"],
                    incident["scenario_id"], incident["replication_id"]
                )
            dest = self.entity_node(
                "incident", incident["incident_id"],
                incident["scenario_id"], incident["replication_id"]
            )
            if origin is not None and dest is not None:
                key = (int(origin), int(dest))
                if key not in self.cache:
                    try:
                        import networkx as nx
                        tt = nx.shortest_path_length(self.G, int(origin), int(dest), weight="travel_time")
                        dist = nx.shortest_path_length(self.G, int(origin), int(dest), weight="length")
                        self.cache[key] = (float(dist)/1000.0, float(tt)/60.0)
                    except Exception:
                        self.cache[key] = (np.nan, np.nan)
                d, ff = self.cache[key]
                if np.isfinite(d) and np.isfinite(ff):
                    return TravelLeg(d, ff, ff*gamma, "osmnx_dynamic")
            return TravelLeg(np.nan, np.nan, np.nan, "osmnx_unreachable", False)

        d = haversine_km(vehicle["lat"], vehicle["lon"], incident["incident_lat"], incident["incident_lon"])
        ff = d / self.cfg.haversine_speed_vehicle_incident_kph * 60.0
        return TravelLeg(d, ff, ff*gamma, "haversine_smoke_test")

    def incident_to_hospital(self, incident, hospital) -> TravelLeg:
        gamma = self._gamma(incident["scenario_id"], incident["timestamp"])
        if self.mode == "osmnx":
            q = self.ih[
                (self.ih["scenario_id"] == incident["scenario_id"]) &
                (self.ih["replication_id"] == incident["replication_id"]) &
                (self.ih["incident_id"] == incident["incident_id"]) &
                (self.ih["hospital_id"] == hospital["hospital_id"])
            ]
            if q.empty or not bool(q.iloc[0].get("reachable", True)):
                return TravelLeg(np.nan, np.nan, np.nan, "osmnx_unreachable", False)
            r = q.iloc[0]
            d = float(r["network_shortest_distance_m"]) / 1000.0
            ff = float(r["freeflow_shortest_travel_time_s"]) / 60.0
            return TravelLeg(d, ff, ff*gamma, "osmnx_od")

        d = haversine_km(incident["incident_lat"], incident["incident_lon"], hospital["latitude"], hospital["longitude"])
        ff = d / self.cfg.haversine_speed_incident_hospital_kph * 60.0
        return TravelLeg(d, ff, ff*gamma, "haversine_smoke_test")

    def hospital_to_charger(self, hospital, charger) -> TravelLeg:
        if self.mode == "osmnx":
            q = self.hc[(self.hc["hospital_id"] == hospital["hospital_id"]) & (self.hc["charger_id"] == charger["charger_id"])]
            if q.empty or not bool(q.iloc[0].get("reachable", True)):
                return TravelLeg(np.nan, np.nan, np.nan, "osmnx_unreachable", False)
            r = q.iloc[0]
            return TravelLeg(
                float(r["network_shortest_distance_m"])/1000.0,
                float(r["freeflow_shortest_travel_time_s"])/60.0,
                float(r["freeflow_shortest_travel_time_s"])/60.0,
                "osmnx_od",
            )
        d = haversine_km(hospital["latitude"], hospital["longitude"], charger["latitude"], charger["longitude"])
        ff = d / self.cfg.haversine_speed_hospital_charger_kph * 60.0
        return TravelLeg(d, ff, ff, "haversine_smoke_test")
