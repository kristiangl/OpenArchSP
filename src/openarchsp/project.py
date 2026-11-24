"""Portable editable project archive. The embedded DXF uses original drawing units."""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from openarchsp.vehicle_kinematics import CombinationGeometry, SimulationResult, simulate_combination
from openarchsp.vehicles import VehicleDefinition
from openarchsp.swept_path import swept_footprint
from shapely.affinity import scale as scale_geometry


@dataclass(frozen=True)
class PathSettings:
    length: float
    width: float
    corner_radius: float
    speed: float
    steering_limit: float
    lock_to_lock: float
    dry_steering: bool = False
    reverse: bool = False


@dataclass
class SweptPathDocument:
    id: str
    name: str
    vehicle_id: str
    color: str
    points: list[tuple[float, float]]
    simulation_result: SimulationResult | None = None
    envelope_geometry: object | None = None
    simulation_scale: float = 1.0
    vehicle_stations: list[float] = field(default_factory=list)
    settings: PathSettings | None = None
    detailed: bool = False
    clearance_summary: dict | None = None


def calculate_path(route, geometry, settings: PathSettings, units: float, fast: bool):
    if geometry is None:
        return None, swept_footprint(route, settings.length * units, settings.width * units,
                                     max(0.25 * units, 1e-6))
    result = simulate_combination(
        [(x / units, y / units) for x, y in route],
        replace(geometry, max_steer_deg=settings.steering_limit),
        corner_radius_m=settings.corner_radius, spacing_m=2.0 if fast else 0.25,
        speed_kmh=settings.speed, lock_to_lock_s=settings.lock_to_lock,
        dry_steering=settings.dry_steering,
        reverse=settings.reverse,
        integration_step_s=0.32 if fast else 0.05, envelope_pose_stride=4 if fast else 1,
    )
    return result, scale_geometry(result.envelope, xfact=units, yfact=units, origin=(0, 0))


@dataclass
class Project:
    paths: list[SweptPathDocument]
    active_id: str
    units: float
    vehicles: dict[str, VehicleDefinition]
    geometries: dict[str, CombinationGeometry]
    display: dict[str, bool]
    clearance_layers: list[str] = field(default_factory=list)


def save_project(target: Path, background: Path, project: Project) -> None:
    paths = []
    for path in project.paths:
        paths.append(dict(id=path.id, name=path.name, vehicle_id=path.vehicle_id,
                          color=path.color, points=path.points, stations=path.vehicle_stations,
                          settings=asdict(path.settings), clearance_summary=path.clearance_summary))
    used = {path.vehicle_id for path in project.paths}
    manifest = dict(version=1, paths=paths, active_id=project.active_id, units=project.units,
                    vehicles=[project.vehicles[key].to_dict() for key in sorted(used)],
                    geometries={k: v.to_dict() for k, v in project.geometries.items() if k in used},
                    display=project.display, clearance_layers=project.clearance_layers)
    # Replace only after both members have been written successfully.
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temp:
        temporary = Path(temp.name)
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("project.json", json.dumps(manifest, allow_nan=False))
            archive.write(background, "background.dxf")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def load_project(source: Path, background: Path) -> Project:
    with ZipFile(source) as archive:
        data = json.loads(archive.read("project.json"))
        if data["version"] != 1:
            raise ValueError("This project version is unsupported.")
        vehicles = {v.id: v for v in map(VehicleDefinition.from_dict, data["vehicles"])}
        geometries = {k: CombinationGeometry.from_dict(v) for k, v in data["geometries"].items()}
        units = float(data["units"])
        if not 0.000001 <= units <= 1_000_000_000:
            raise ValueError("Invalid project drawing scale.")
        paths = []
        for entry in data["paths"]:
            settings = PathSettings(**{
                k: bool(v) if k in ("dry_steering", "reverse") else float(v)
                for k, v in entry["settings"].items()
            })
            points = [(float(x), float(y)) for x, y in entry["points"]]
            stations = [float(value) for value in entry["stations"]]
            if (entry["vehicle_id"] not in vehicles
                    or not all(math.isfinite(v) for xy in points for v in xy)
                    or not all(0 <= value <= 1 for value in stations)
                    or not all(
                        math.isfinite(getattr(settings, key)) and getattr(settings, key) > 0
                        for key in ("length", "width", "corner_radius", "speed", "steering_limit", "lock_to_lock")
                    )):
                raise ValueError("Invalid swept path in project.")
            for key in ("id", "name", "color"):
                if not isinstance(entry[key], str) or not entry[key]:
                    raise ValueError("Invalid swept path details.")
            paths.append(SweptPathDocument(entry["id"], entry["name"], entry["vehicle_id"],
                                           entry["color"], points, vehicle_stations=stations,
                                           settings=settings, simulation_scale=units,
                                           clearance_summary=entry.get("clearance_summary")))
        ids = {path.id for path in paths}
        if len(ids) != len(paths) or data["active_id"] not in ids:
            raise ValueError("Invalid project path selection.")
        display = data["display"]
        if not isinstance(display, dict) or not all(isinstance(v, bool) for v in display.values()):
            raise ValueError("Invalid project display options.")
        with archive.open("background.dxf") as member, background.open("wb") as dest:
            shutil.copyfileobj(member, dest)
    clearance_layers = data.get("clearance_layers", [])
    if not isinstance(clearance_layers, list) or not all(isinstance(x, str) for x in clearance_layers):
        raise ValueError("Invalid clearance layer selection.")
    return Project(paths, data["active_id"], units, vehicles, geometries, display, clearance_layers)
