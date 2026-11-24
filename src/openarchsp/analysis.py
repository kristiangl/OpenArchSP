"""Drawing-layer clearance analysis helpers."""

from __future__ import annotations

import math
from collections.abc import Iterable

from ezdxf.document import Drawing
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union


def modelspace_layers(document: Drawing) -> list[str]:
    """Return non-OpenArch layers containing modelspace entities."""
    return sorted(
        {
            str(entity.dxf.layer)
            for entity in document.modelspace()
            if not str(entity.dxf.layer).upper().startswith("OASP")
        },
        key=str.casefold,
    )


def _entity_geometry(entity: object) -> BaseGeometry | None:
    kind = entity.dxftype()
    if kind == "LINE":
        return LineString(
            ((entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y))
        )
    if kind in ("LWPOLYLINE", "POLYLINE"):
        if kind == "LWPOLYLINE":
            vertices = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
            closed = bool(entity.closed)
        else:
            vertices = [
                (float(v.dxf.location.x), float(v.dxf.location.y))
                for v in entity.vertices
            ]
            closed = bool(entity.is_closed)
        if len(vertices) < 2:
            return None
        if closed and len(vertices) >= 3:
            shape = Polygon(vertices)
            return shape if shape.is_valid else shape.buffer(0)
        return LineString(vertices)
    if kind == "CIRCLE":
        center = entity.dxf.center
        radius = float(entity.dxf.radius)
        points = [
            (
                center.x + radius * math.cos(math.tau * index / 64),
                center.y + radius * math.sin(math.tau * index / 64),
            )
            for index in range(64)
        ]
        return Polygon(points)
    if kind == "ARC":
        center = entity.dxf.center
        radius = float(entity.dxf.radius)
        start = math.radians(float(entity.dxf.start_angle))
        sweep = math.radians(
            (float(entity.dxf.end_angle) - float(entity.dxf.start_angle)) % 360
        )
        steps = max(8, math.ceil(math.degrees(sweep) / 4))
        return LineString(
            (
                center.x + radius * math.cos(start + sweep * index / steps),
                center.y + radius * math.sin(start + sweep * index / steps),
            )
            for index in range(steps + 1)
        )
    if kind == "POINT":
        return Point(entity.dxf.location.x, entity.dxf.location.y)
    return None


def layer_geometries(document: Drawing, layers: Iterable[str]) -> dict[str, BaseGeometry]:
    """Convert supported entities on selected CAD layers to Shapely geometries."""
    selected = set(layers)
    shapes: dict[str, list[BaseGeometry]] = {layer: [] for layer in selected}
    for entity in document.modelspace():
        layer = str(entity.dxf.layer)
        if layer not in selected:
            continue
        shape = _entity_geometry(entity)
        if shape is not None and not shape.is_empty:
            shapes[layer].append(shape)
    return {
        layer: unary_union(layer_shapes)
        for layer, layer_shapes in shapes.items()
        if layer_shapes
    }


def check_envelope_clearance(
    envelope: BaseGeometry,
    document: Drawing,
    layers: Iterable[str],
    drawing_units_per_metre: float,
) -> dict[str, object]:
    """Measure minimum envelope distance to each selected layer in metres."""
    if drawing_units_per_metre <= 0:
        raise ValueError("Drawing units per metre must be positive.")
    per_layer: dict[str, dict[str, float | bool]] = {}
    for layer, obstacles in layer_geometries(document, layers).items():
        collision = envelope.intersects(obstacles)
        clearance_m = 0.0 if collision else envelope.distance(obstacles) / drawing_units_per_metre
        per_layer[layer] = {"collision": collision, "clearance_m": clearance_m}
    collisions = sorted(
        layer for layer, result in per_layer.items() if result["collision"]
    )
    minimum = min(
        (float(result["clearance_m"]) for result in per_layer.values()),
        default=None,
    )
    return {
        "per_layer": per_layer,
        "collision_layers": collisions,
        "minimum_clearance_m": minimum,
        "checked_layers": sorted(per_layer, key=str.casefold),
    }
