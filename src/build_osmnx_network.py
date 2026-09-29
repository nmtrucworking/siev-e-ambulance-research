#!/usr/bin/env python3
"""Build reproducible OSMnx routing artifacts for the e-ambulance pipeline.

Outputs (under data/osmnx):
- study_graph.graphml
- point_node_map.csv
- snap_qa.csv
- incident_hospital_od.csv.gz
- hospital_charger_od.csv
- network_metadata.json

The graph contains OSMnx-imputed free-flow edge speeds/travel times. It is a
routing baseline, not calibrated HCMC traffic evidence.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BuildConfig:
    root: Path
    network_type: str = "drive"
    review_snap_distance_m: float = 250.0
    force_redownload: bool = False
    build_initial_fleet_incident_od: bool = False

    @property
    def eda_dir(self) -> Path:
        return self.root / "eda_pre_osmnx"

    @property
    def data_dir(self) -> Path:
        return self.root / "data" / "sample"

    @property
    def osm_dir(self) -> Path:
        return self.root / "data" / "osmnx"


def find_project_root(start: Path | None = None) -> Path:
    start = (start or Path.cwd()).resolve()
    candidates = [start, start.parent, *start.parents]
    module_root = Path(__file__).resolve().parents[1]
    if module_root not in candidates:
        candidates.insert(0, module_root)
    for path in candidates:
        if (path / "data" / "sample").exists() and (path / "eda_pre_osmnx").exists():
            return path
    raise FileNotFoundError("Cannot find project root containing data/sample and eda_pre_osmnx")


def _required_inputs(cfg: BuildConfig) -> list[Path]:
    return [
        cfg.eda_dir / "osmnx_points.csv",
        cfg.eda_dir / "osmnx_bbox.json",
        cfg.data_dir / "incidents.csv.gz",
        cfg.data_dir / "hospitals.csv",
        cfg.data_dir / "charging_stations.csv",
        cfg.data_dir / "fleet_initial.csv.gz",
    ]


def precheck(cfg: BuildConfig) -> dict[str, Any]:
    missing = [str(p) for p in _required_inputs(cfg) if not p.exists()]
    if missing:
        raise FileNotFoundError("Missing required inputs:\n" + "\n".join(missing))

    import osmnx as ox

    points = pd.read_csv(cfg.eda_dir / "osmnx_points.csv")
    bbox_cfg = json.loads((cfg.eda_dir / "osmnx_bbox.json").read_text(encoding="utf-8"))
    bbox = bbox_cfg["suggested_graph_download_bbox_with_2km_margin"]

    counts = points["point_type"].value_counts().to_dict()
    result = {
        "root": str(cfg.root),
        "osmnx_version": ox.__version__,
        "network_type": cfg.network_type,
        "points": int(len(points)),
        "point_type_counts": {str(k): int(v) for k, v in counts.items()},
        "bbox": {
            "west": float(bbox["west"]),
            "south": float(bbox["south"]),
            "east": float(bbox["east"]),
            "north": float(bbox["north"]),
        },
        "existing_graph": (cfg.osm_dir / "study_graph.graphml").exists(),
    }
    return result


def _load_inputs(cfg: BuildConfig):
    check = precheck(cfg)
    points = pd.read_csv(cfg.eda_dir / "osmnx_points.csv")
    bbox_cfg = json.loads((cfg.eda_dir / "osmnx_bbox.json").read_text(encoding="utf-8"))
    inc = pd.read_csv(cfg.data_dir / "incidents.csv.gz")
    hosp = pd.read_csv(cfg.data_dir / "hospitals.csv")
    chg = pd.read_csv(cfg.data_dir / "charging_stations.csv")
    fleet = pd.read_csv(cfg.data_dir / "fleet_initial.csv.gz")
    return check, points, bbox_cfg, inc, hosp, chg, fleet


def _build_or_load_graph(cfg: BuildConfig, bbox_cfg: dict[str, Any]):
    import osmnx as ox

    b = bbox_cfg["suggested_graph_download_bbox_with_2km_margin"]
    bbox = (float(b["west"]), float(b["south"]), float(b["east"]), float(b["north"]))
    graph_path = cfg.osm_dir / "study_graph.graphml"

    ox.settings.use_cache = True
    ox.settings.log_console = True

    if graph_path.exists() and not cfg.force_redownload:
        t0 = time.time()
        graph = ox.io.load_graphml(graph_path)
        source = "graphml_cache"
        print(f"Loaded graph cache in {time.time() - t0:.2f}s: {graph_path}")
    else:
        t0 = time.time()
        graph = ox.graph.graph_from_bbox(
            bbox,
            network_type=cfg.network_type,
            simplify=True,
            retain_all=False,
            truncate_by_edge=True,
        )
        source = "overpass_download"
        print(f"Downloaded graph in {time.time() - t0:.2f}s")
        graph = ox.routing.add_edge_speeds(graph)
        graph = ox.routing.add_edge_travel_times(graph)
        ox.io.save_graphml(graph, graph_path)
        print(f"Saved: {graph_path}")

    edge = next(iter(graph.edges(data=True)))[2]
    changed = False
    if "speed_kph" not in edge:
        graph = ox.routing.add_edge_speeds(graph)
        changed = True
    if "travel_time" not in edge:
        graph = ox.routing.add_edge_travel_times(graph)
        changed = True
    if changed:
        ox.io.save_graphml(graph, graph_path)

    return graph, source, bbox


def _edge_qa(graph: nx.MultiDiGraph) -> dict[str, Any]:
    rows = []
    for _, _, _, d in graph.edges(keys=True, data=True):
        rows.append((d.get("length"), d.get("speed_kph"), d.get("travel_time")))
    edge_df = pd.DataFrame(rows, columns=["length_m", "speed_kph", "travel_time_s"])
    return {
        "edge_count": int(len(edge_df)),
        "missing_length": int(edge_df["length_m"].isna().sum()),
        "missing_speed_kph": int(edge_df["speed_kph"].isna().sum()),
        "missing_travel_time_s": int(edge_df["travel_time_s"].isna().sum()),
        "speed_kph_median": float(edge_df["speed_kph"].median()),
        "travel_time_s_median": float(edge_df["travel_time_s"].median()),
    }


def _snap_points(cfg: BuildConfig, graph: nx.MultiDiGraph, points: pd.DataFrame) -> pd.DataFrame:
    import osmnx as ox

    node_ids, snap_dist = ox.distance.nearest_nodes(
        graph,
        X=points["longitude"].to_numpy(),
        Y=points["latitude"].to_numpy(),
        return_dist=True,
    )
    point_map = points.copy()
    point_map["osmid"] = node_ids
    point_map["snap_distance_m"] = snap_dist
    point_map["snap_review_flag"] = point_map["snap_distance_m"] > cfg.review_snap_distance_m
    point_map.to_csv(cfg.osm_dir / "point_node_map.csv", index=False)

    snap_summary = (
        point_map.groupby("point_type", dropna=False)["snap_distance_m"]
        .agg(["count", "mean", "median", "max"])
        .reset_index()
    )
    review = (
        point_map.groupby("point_type", dropna=False)["snap_review_flag"]
        .sum()
        .rename("review_count")
        .reset_index()
    )
    snap_summary = snap_summary.merge(review, on="point_type", how="left")
    snap_summary.to_csv(cfg.osm_dir / "snap_qa.csv", index=False)
    return point_map


def _split_nodes(point_map: pd.DataFrame):
    incidents = (
        point_map.loc[point_map["point_type"] == "incident", ["entity_id", "scenario_id", "replication_id", "osmid", "snap_distance_m"]]
        .rename(columns={"entity_id": "incident_id", "osmid": "incident_node"})
    )
    hospitals = (
        point_map.loc[point_map["point_type"] == "hospital", ["entity_id", "osmid", "snap_distance_m"]]
        .rename(columns={"entity_id": "hospital_id", "osmid": "hospital_node"})
    )
    chargers = (
        point_map.loc[point_map["point_type"] == "charger", ["entity_id", "osmid", "snap_distance_m"]]
        .rename(columns={"entity_id": "charger_id", "osmid": "charger_node"})
    )
    fleet = (
        point_map.loc[point_map["point_type"] == "fleet_initial", ["entity_id", "scenario_id", "replication_id", "osmid", "snap_distance_m"]]
        .rename(columns={"entity_id": "vehicle_id", "osmid": "vehicle_node"})
    )
    return incidents, hospitals, chargers, fleet


def _build_incident_hospital_od(
    cfg: BuildConfig,
    graph: nx.MultiDiGraph,
    incidents: pd.DataFrame,
    hospitals: pd.DataFrame,
) -> pd.DataFrame:
    reverse_graph = graph.reverse(copy=False)
    rows: list[dict[str, Any]] = []
    for _, h in hospitals.iterrows():
        h_id = h["hospital_id"]
        h_node = int(h["hospital_node"])
        tt = nx.single_source_dijkstra_path_length(reverse_graph, h_node, weight="travel_time")
        dist = nx.single_source_dijkstra_path_length(reverse_graph, h_node, weight="length")
        for _, i in incidents.iterrows():
            i_node = int(i["incident_node"])
            t = tt.get(i_node, np.nan)
            d = dist.get(i_node, np.nan)
            rows.append({
                "scenario_id": i["scenario_id"],
                "replication_id": i["replication_id"],
                "incident_id": i["incident_id"],
                "incident_node": i_node,
                "hospital_id": h_id,
                "hospital_node": h_node,
                "freeflow_shortest_travel_time_s": t,
                "network_shortest_distance_m": d,
                "reachable": bool(np.isfinite(t) and np.isfinite(d)),
            })
    od = pd.DataFrame(rows)
    od.to_csv(cfg.osm_dir / "incident_hospital_od.csv.gz", index=False, compression="gzip")
    return od


def _build_hospital_charger_od(
    cfg: BuildConfig,
    graph: nx.MultiDiGraph,
    hospitals: pd.DataFrame,
    chargers: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    charger_lookup = {r["charger_id"]: int(r["charger_node"]) for _, r in chargers.iterrows()}
    for _, h in hospitals.iterrows():
        h_id = h["hospital_id"]
        h_node = int(h["hospital_node"])
        tt = nx.single_source_dijkstra_path_length(graph, h_node, weight="travel_time")
        dist = nx.single_source_dijkstra_path_length(graph, h_node, weight="length")
        for c_id, c_node in charger_lookup.items():
            t = tt.get(c_node, np.nan)
            d = dist.get(c_node, np.nan)
            rows.append({
                "hospital_id": h_id,
                "hospital_node": h_node,
                "charger_id": c_id,
                "charger_node": c_node,
                "freeflow_shortest_travel_time_s": t,
                "network_shortest_distance_m": d,
                "reachable": bool(np.isfinite(t) and np.isfinite(d)),
            })
    od = pd.DataFrame(rows)
    od.to_csv(cfg.osm_dir / "hospital_charger_od.csv", index=False)
    return od


def _build_initial_fleet_incident_od(
    cfg: BuildConfig,
    graph: nx.MultiDiGraph,
    incidents: pd.DataFrame,
    fleet: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    incidents_by_rep = {
        key: grp for key, grp in incidents.groupby(["scenario_id", "replication_id"])
    }
    for _, v in fleet.iterrows():
        key = (v["scenario_id"], v["replication_id"])
        target = incidents_by_rep.get(key)
        if target is None:
            continue
        v_node = int(v["vehicle_node"])
        tt = nx.single_source_dijkstra_path_length(graph, v_node, weight="travel_time")
        dist = nx.single_source_dijkstra_path_length(graph, v_node, weight="length")
        for _, i in target.iterrows():
            i_node = int(i["incident_node"])
            t = tt.get(i_node, np.nan)
            d = dist.get(i_node, np.nan)
            rows.append({
                "scenario_id": v["scenario_id"],
                "replication_id": v["replication_id"],
                "vehicle_id": v["vehicle_id"],
                "vehicle_node": v_node,
                "incident_id": i["incident_id"],
                "incident_node": i_node,
                "freeflow_shortest_travel_time_s": t,
                "network_shortest_distance_m": d,
                "reachable": bool(np.isfinite(t) and np.isfinite(d)),
                "reference_only": True,
            })
    od = pd.DataFrame(rows)
    od.to_csv(cfg.osm_dir / "fleet_incident_initial_od.csv.gz", index=False, compression="gzip")
    return od


def _haversine_m(lat1, lon1, lat2, lon2):
    radius = 6_371_000.0
    lat1 = np.radians(lat1)
    lon1 = np.radians(lon1)
    lat2 = np.radians(lat2)
    lon2 = np.radians(lon2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * radius * np.arcsin(np.sqrt(a))


def _od_sanity(od: pd.DataFrame, incidents_raw: pd.DataFrame, hospitals_raw: pd.DataFrame) -> dict[str, Any]:
    inc_xy = incidents_raw[["incident_id", "incident_lat", "incident_lon"]].drop_duplicates("incident_id")
    hosp_xy = hospitals_raw[["hospital_id", "latitude", "longitude"]]
    qa = od.merge(inc_xy, on="incident_id", how="left").merge(hosp_xy, on="hospital_id", how="left")
    qa["haversine_m"] = _haversine_m(qa["incident_lat"], qa["incident_lon"], qa["latitude"], qa["longitude"])
    qa["network_to_haversine_ratio"] = qa["network_shortest_distance_m"] / qa["haversine_m"]
    ratio = qa.loc[
        qa["reachable"] & np.isfinite(qa["network_to_haversine_ratio"]),
        "network_to_haversine_ratio",
    ]
    reachable_per_incident = od.groupby("incident_id")["reachable"].sum()
    return {
        "rows": int(len(od)),
        "reachable_rate": float(od["reachable"].mean()),
        "unreachable_count": int((~od["reachable"]).sum()),
        "incidents_total": int(reachable_per_incident.size),
        "incidents_with_zero_reachable_hospitals": int((reachable_per_incident == 0).sum()),
        "incidents_with_partial_reachability": int(
            ((reachable_per_incident > 0) & (reachable_per_incident < od["hospital_id"].nunique())).sum()
        ),
        "min_reachable_hospitals_per_incident": int(reachable_per_incident.min()),
        "network_to_haversine_ratio_median": float(ratio.median()) if len(ratio) else math.nan,
        "network_to_haversine_ratio_p95": float(ratio.quantile(0.95)) if len(ratio) else math.nan,
    }


def build_network_artifacts(cfg: BuildConfig) -> dict[str, Any]:
    import osmnx as ox

    cfg.osm_dir.mkdir(parents=True, exist_ok=True)
    pre, points, bbox_cfg, inc, hosp, _chg, _fleet = _load_inputs(cfg)
    graph, graph_source, bbox = _build_or_load_graph(cfg, bbox_cfg)
    edge_qa = _edge_qa(graph)
    point_map = _snap_points(cfg, graph, points)
    incidents, hospitals, chargers, fleet = _split_nodes(point_map)

    print(
        f"Snapped points: incidents={len(incidents)}, hospitals={len(hospitals)}, "
        f"chargers={len(chargers)}, fleet={len(fleet)}"
    )

    incident_hospital = _build_incident_hospital_od(cfg, graph, incidents, hospitals)
    hospital_charger = _build_hospital_charger_od(cfg, graph, hospitals, chargers)
    if cfg.build_initial_fleet_incident_od:
        _build_initial_fleet_incident_od(cfg, graph, incidents, fleet)

    snap_review_count = int(point_map["snap_review_flag"].sum())
    od_qa = _od_sanity(incident_hospital, inc, hosp)

    metadata = {
        "osmnx_version": ox.__version__,
        "network_type": cfg.network_type,
        "bbox_order": ["west", "south", "east", "north"],
        "bbox": {"west": bbox[0], "south": bbox[1], "east": bbox[2], "north": bbox[3]},
        "graph_source": graph_source,
        "nodes": int(graph.number_of_nodes()),
        "edges": int(graph.number_of_edges()),
        "graph_crs": str(graph.graph.get("crs")),
        "speed_model": "OSMnx add_edge_speeds default imputation; requires later local calibration",
        "travel_time_model": "edge length / speed_kph free-flow baseline",
        "traffic_applied": False,
        "emergency_vehicle_priority_modelled": False,
        "snap_review_threshold_m": cfg.review_snap_distance_m,
        "snap_review_count": snap_review_count,
        "snap_review_rate": float(snap_review_count / len(point_map)),
        "snap_max_distance_m": float(point_map["snap_distance_m"].max()),
        "edge_qa": edge_qa,
        "incident_hospital_qa": od_qa,
        "hospital_charger_rows": int(len(hospital_charger)),
        "hospital_charger_reachable_rate": float(hospital_charger["reachable"].mean()),
        "precheck": pre,
        "generated_files": [
            "study_graph.graphml",
            "point_node_map.csv",
            "snap_qa.csv",
            "incident_hospital_od.csv.gz",
            "hospital_charger_od.csv",
            *(["fleet_incident_initial_od.csv.gz"] if cfg.build_initial_fleet_incident_od else []),
        ],
    }
    metadata_path = cfg.osm_dir / "network_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps(metadata, indent=2, allow_nan=True))
    return metadata


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None, help="Project root; auto-detected by default")
    parser.add_argument("--precheck", action="store_true", help="Validate inputs/environment without downloading/building graph")
    parser.add_argument("--force-redownload", action="store_true", help="Ignore graph cache and download from Overpass again")
    parser.add_argument("--network-type", default="drive")
    parser.add_argument("--review-snap-distance-m", type=float, default=250.0)
    parser.add_argument("--build-initial-fleet-incident-od", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    root = (args.root.resolve() if args.root else find_project_root())
    cfg = BuildConfig(
        root=root,
        network_type=args.network_type,
        review_snap_distance_m=args.review_snap_distance_m,
        force_redownload=args.force_redownload,
        build_initial_fleet_incident_od=args.build_initial_fleet_incident_od,
    )
    if args.precheck:
        print(json.dumps(precheck(cfg), indent=2))
        return 0
    build_network_artifacts(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
