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
    1) OSMnx cache / graph if canonical build artifacts exist.
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
        self.node_lookup = {}
        self.ih_lookup = {}
        self.hc_lookup = {}
        self.replication_incident_nodes = {}
        self.replication_fleet_nodes = {}
        self.fixed_origin_nodes = set()
        self._sparse_node_index = None
        self._travel_time_csr = None
        self._length_csr = None
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
                for r in self.point_map.itertuples(index=False):
                    scenario_id = None if pd.isna(r.scenario_id) else str(r.scenario_id)
                    replication_id = None if pd.isna(r.replication_id) else str(r.replication_id)
                    key = (str(r.point_type), str(r.entity_id), scenario_id, replication_id)
                    node_id = int(r.osmid)
                    self.node_lookup.setdefault(key, node_id)
                    if str(r.point_type) == "incident" and scenario_id is not None and replication_id is not None:
                        self.replication_incident_nodes.setdefault((scenario_id, replication_id), set()).add(node_id)
                    elif str(r.point_type) == "fleet_initial" and scenario_id is not None and replication_id is not None:
                        self.replication_fleet_nodes.setdefault((scenario_id, replication_id), set()).add(node_id)
                    elif str(r.point_type) in {"hospital", "charger"}:
                        self.fixed_origin_nodes.add(node_id)
                for r in self.ih.itertuples(index=False):
                    key = (
                        str(r.scenario_id),
                        str(r.replication_id),
                        str(r.incident_id),
                        str(r.hospital_id),
                    )
                    self.ih_lookup[key] = (
                        bool(r.reachable),
                        float(r.network_shortest_distance_m),
                        float(r.freeflow_shortest_travel_time_s),
                    )
                for r in self.hc.itertuples(index=False):
                    key = (str(r.hospital_id), str(r.charger_id))
                    self.hc_lookup[key] = (
                        bool(r.reachable),
                        float(r.network_shortest_distance_m),
                        float(r.freeflow_shortest_travel_time_s),
                    )
                self.mode = "osmnx"
            except Exception as e:
                if not allow_haversine_fallback:
                    raise RuntimeError(f"OSMnx files exist but could not be loaded: {e}") from e

        if self.mode is None:
            if not allow_haversine_fallback:
                raise FileNotFoundError(
                    "OSMnx routing artifacts are missing. Run `python src/build_osmnx_network.py`, "
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
        return self.node_lookup.get((
            str(point_type),
            str(entity_id),
            None if scenario_id is None else str(scenario_id),
            None if replication_id is None else str(replication_id),
        ))

    def _ensure_sparse_graphs(self) -> None:
        if self._travel_time_csr is not None and self._length_csr is not None:
            return
        if self.mode != "osmnx":
            return

        from scipy.sparse import csr_matrix

        node_ids = [int(node) for node in self.G.nodes]
        self._sparse_node_index = {node_id: idx for idx, node_id in enumerate(node_ids)}
        travel_min = {}
        length_min = {}
        for u, v, _key, data in self.G.edges(keys=True, data=True):
            ui = self._sparse_node_index[int(u)]
            vi = self._sparse_node_index[int(v)]
            pair = (ui, vi)
            travel_time = float(data.get("travel_time", np.inf))
            length = float(data.get("length", np.inf))
            if np.isfinite(travel_time):
                previous = travel_min.get(pair)
                if previous is None or travel_time < previous:
                    travel_min[pair] = travel_time
            if np.isfinite(length):
                previous = length_min.get(pair)
                if previous is None or length < previous:
                    length_min[pair] = length

        shape = (len(node_ids), len(node_ids))
        travel_rows = [pair[0] for pair in travel_min]
        travel_cols = [pair[1] for pair in travel_min]
        travel_values = list(travel_min.values())
        length_rows = [pair[0] for pair in length_min]
        length_cols = [pair[1] for pair in length_min]
        length_values = list(length_min.values())
        self._travel_time_csr = csr_matrix(
            (travel_values, (travel_rows, travel_cols)), shape=shape, dtype=float
        )
        self._length_csr = csr_matrix(
            (length_values, (length_rows, length_cols)), shape=shape, dtype=float
        )

    def prefetch_vehicle_to_incident(self, scenario_id: str, replication_id: str) -> dict:
        """Fill exact OSMnx route cache for all possible vehicle origins in one replication."""
        if self.mode != "osmnx":
            return {"origins": 0, "destinations": 0, "cache_entries_added": 0}

        group = (str(scenario_id), str(replication_id))
        destinations = sorted(self.replication_incident_nodes.get(group, set()))
        origins = sorted(self.replication_fleet_nodes.get(group, set()) | self.fixed_origin_nodes)
        if not origins or not destinations:
            return {"origins": len(origins), "destinations": len(destinations), "cache_entries_added": 0}

        self._ensure_sparse_graphs()
        from scipy.sparse.csgraph import dijkstra

        origin_indices = [self._sparse_node_index[node] for node in origins]
        destination_indices = [self._sparse_node_index[node] for node in destinations]
        travel_distances = dijkstra(
            self._travel_time_csr,
            directed=True,
            indices=origin_indices,
            return_predecessors=False,
        )
        length_distances = dijkstra(
            self._length_csr,
            directed=True,
            indices=origin_indices,
            return_predecessors=False,
        )

        added = 0
        for origin_pos, origin in enumerate(origins):
            for destination_pos, destination in enumerate(destinations):
                key = (origin, destination)
                if key in self.cache:
                    continue
                graph_pos = destination_indices[destination_pos]
                travel_time_s = float(travel_distances[origin_pos, graph_pos])
                distance_m = float(length_distances[origin_pos, graph_pos])
                if np.isfinite(travel_time_s) and np.isfinite(distance_m):
                    self.cache[key] = (distance_m / 1000.0, travel_time_s / 60.0)
                else:
                    self.cache[key] = (np.nan, np.nan)
                added += 1

        return {
            "origins": len(origins),
            "destinations": len(destinations),
            "cache_entries_added": added,
        }

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
            r = self.ih_lookup.get((
                str(incident["scenario_id"]),
                str(incident["replication_id"]),
                str(incident["incident_id"]),
                str(hospital["hospital_id"]),
            ))
            if r is None or not r[0]:
                return TravelLeg(np.nan, np.nan, np.nan, "osmnx_unreachable", False)
            _, distance_m, travel_time_s = r
            d = distance_m / 1000.0
            ff = travel_time_s / 60.0
            return TravelLeg(d, ff, ff*gamma, "osmnx_od")

        d = haversine_km(incident["incident_lat"], incident["incident_lon"], hospital["latitude"], hospital["longitude"])
        ff = d / self.cfg.haversine_speed_incident_hospital_kph * 60.0
        return TravelLeg(d, ff, ff*gamma, "haversine_smoke_test")

    def hospital_to_charger(self, hospital, charger) -> TravelLeg:
        if self.mode == "osmnx":
            r = self.hc_lookup.get((str(hospital["hospital_id"]), str(charger["charger_id"])))
            if r is None or not r[0]:
                return TravelLeg(np.nan, np.nan, np.nan, "osmnx_unreachable", False)
            _, distance_m, travel_time_s = r
            return TravelLeg(
                distance_m / 1000.0,
                travel_time_s / 60.0,
                travel_time_s / 60.0,
                "osmnx_od",
            )
        d = haversine_km(hospital["latitude"], hospital["longitude"], charger["latitude"], charger["longitude"])
        ff = d / self.cfg.haversine_speed_hospital_charger_kph * 60.0
        return TravelLeg(d, ff, ff, "haversine_smoke_test")
