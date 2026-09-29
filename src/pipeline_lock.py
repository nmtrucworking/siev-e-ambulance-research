#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


LOCK_VERSION = 1
CORE_FILES = [
    "configs/experiment_30_paired.json",
    "configs/runtime_model.json",
    "data/parameter_registry.csv",
    "data/sample/incidents.csv.gz",
    "data/sample/scenarios.csv.gz",
    "data/sample/hospitals.csv",
    "data/sample/fleet_initial.csv.gz",
    "data/sample/hospital_capacity_timeseries.csv.gz",
    "data/sample/charging_stations.csv",
    "data/sample/replications.csv.gz",
    "data/osmnx/study_graph.graphml",
    "data/osmnx/point_node_map.csv",
    "data/osmnx/snap_qa.csv",
    "data/osmnx/incident_hospital_od.csv.gz",
    "data/osmnx/hospital_charger_od.csv",
    "data/osmnx/network_metadata.json",
    "requirements.txt",
    "requirements-full.txt",
    "scripts/run_30_paired_osmnx.sh",
]
PACKAGE_NAMES = [
    "numpy",
    "pandas",
    "scipy",
    "matplotlib",
    "osmnx",
    "networkx",
    "scikit-learn",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_value(root: Path, *args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def lock_files(root: Path) -> list[Path]:
    files = [root / rel for rel in CORE_FILES]
    files.extend(sorted((root / "src").glob("*.py")))
    missing = [p for p in files if not p.is_file()]
    if missing:
        joined = "\n".join(str(p.relative_to(root)) for p in missing)
        raise FileNotFoundError(f"Missing files required for final pipeline lock:\n{joined}")
    return sorted(set(files))


def package_versions() -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for name in PACKAGE_NAMES:
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = None
    return result


def build_lock(root: Path) -> dict:
    files = lock_files(root)
    return {
        "lock_version": LOCK_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Frozen final 30-paired OSMnx experiment pipeline",
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "packages": package_versions(),
        "git": {
            "head": git_value(root, "rev-parse", "HEAD"),
            "status_porcelain_at_lock": git_value(root, "status", "--porcelain"),
        },
        "files": {
            str(path.relative_to(root)): {
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in files
        },
    }


def verify_lock(root: Path, lock_path: Path) -> list[str]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    errors: list[str] = []

    if lock.get("lock_version") != LOCK_VERSION:
        errors.append(
            f"lock_version changed: expected {LOCK_VERSION}, got {lock.get('lock_version')}"
        )

    expected_python = lock.get("python", {}).get("version")
    if expected_python != platform.python_version():
        errors.append(
            f"Python version changed: expected {expected_python}, got {platform.python_version()}"
        )

    current_packages = package_versions()
    for name, expected in lock.get("packages", {}).items():
        actual = current_packages.get(name)
        if actual != expected:
            errors.append(f"package {name} changed: expected {expected}, got {actual}")

    for rel, metadata in lock.get("files", {}).items():
        path = root / rel
        if not path.is_file():
            errors.append(f"missing locked file: {rel}")
            continue
        actual = sha256(path)
        if actual != metadata.get("sha256"):
            errors.append(
                f"hash mismatch for {rel}: expected {metadata.get('sha256')}, got {actual}"
            )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or verify the frozen final experiment pipeline lock")
    parser.add_argument("action", choices=["create", "verify"])
    parser.add_argument("--root", default=".")
    parser.add_argument("--lock", default="provenance/final_pipeline_lock.json")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    lock_path = Path(args.lock)
    if not lock_path.is_absolute():
        lock_path = root / lock_path

    if args.action == "create":
        if lock_path.exists() and not args.force:
            raise FileExistsError(f"Lock already exists: {lock_path}. Use --force to replace it intentionally.")
        payload = build_lock(root)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps({
            "created": str(lock_path),
            "locked_files": len(payload["files"]),
            "python": payload["python"],
            "packages": payload["packages"],
            "git_head": payload["git"]["head"],
        }, indent=2))
        return 0

    if not lock_path.is_file():
        raise FileNotFoundError(f"Final pipeline lock is missing: {lock_path}")
    errors = verify_lock(root, lock_path)
    result = {"verified": not errors, "lock": str(lock_path), "errors": errors}
    print(json.dumps(result, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
