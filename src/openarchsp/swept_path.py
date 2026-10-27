"""Rigid-body swept footprint preview along a user-defined reference line."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union


Point = tuple[float, float]


def _rectangle(center: Point, heading: float, length: float, width: float) -> Polygon:
    half_length = length / 2
    half_width = width / 2
    cosine = math.cos(heading)
    sine = math.sin(heading)
    corners: list[Point] = []
    for x, y in (
        (-half_length, -half_width),
        (half_length, -half_width),
        (half_length, half_width),
        (-half_length, half_width),
    ):
        corners.append(
            (
                center[0] + x * cosine - y * sine,
                center[1] + x * sine + y * cosine,
            )
        )
    return Polygon(corners)


def _interpolate(a: Point, b: Point, fraction: float) -> Point:
    return (a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction)


def swept_footprint(
    route: Sequence[Point],
    length: float,
    width: float,
    spacing: float,
    *,
    max_poses: int = 60_000,
) -> BaseGeometry:
    """Union oriented vehicle rectangles sampled along a route.

    The input route is the path of the vehicle body centre. This deliberately
    models a rigid rectangle, not wheel steering or trailer off-tracking.
    """
    if length <= 0 or width <= 0 or spacing <= 0:
        raise ValueError("Vehicle length, width, and sampling spacing must be positive.")

    points: list[Point] = []
    for point in route:
        normalized = (float(point[0]), float(point[1]))
        if not points or math.dist(points[-1], normalized) > 1e-9:
            points.append(normalized)
    if len(points) < 2:
        raise ValueError("Draw a path with at least two distinct points.")

    segments: list[tuple[Point, Point, float, float]] = []
    for start, end in zip(points, points[1:]):
        distance = math.dist(start, end)
        if distance > 1e-9:
            segments.append((start, end, distance, math.atan2(end[1] - start[1], end[0] - start[0])))
    if not segments:
        raise ValueError("The path has no measurable length.")

    pose_count = sum(math.ceil(segment[2] / spacing) + 1 for segment in segments)
    for index in range(1, len(segments)):
        previous_heading = segments[index - 1][3]
        next_heading = segments[index][3]
        delta = (next_heading - previous_heading + math.pi) % (2 * math.pi) - math.pi
        pose_count += max(1, math.ceil(abs(delta) / math.radians(5))) + 1
    if pose_count > max_poses:
        raise ValueError(
            "This path is too long for the current sampling interval. "
            "Shorten the path or increase the interval."
        )

    footprints: list[Polygon] = []
    for index, (start, end, distance, heading) in enumerate(segments):
        steps = max(1, math.ceil(distance / spacing))
        for step in range(steps + 1):
            fraction = step / steps
            footprints.append(
                _rectangle(_interpolate(start, end, fraction), heading, length, width)
            )

        if index < len(segments) - 1:
            next_heading = segments[index + 1][3]
            delta = (next_heading - heading + math.pi) % (2 * math.pi) - math.pi
            turn_steps = max(1, math.ceil(abs(delta) / math.radians(5)))
            for turn_step in range(turn_steps + 1):
                fraction = turn_step / turn_steps
                footprints.append(
                    _rectangle(
                        end,
                        heading + delta * fraction,
                        length,
                        width,
                    )
                )

    result = unary_union(footprints)
    if result.is_empty or not result.is_valid:
        raise ValueError("Could not create a valid vehicle footprint for this path.")
    return result


def polygons(geometry: BaseGeometry) -> Iterable[Polygon]:
    """Yield polygon parts from a Polygon or MultiPolygon result."""
    if geometry.geom_type == "Polygon":
        yield geometry  # type: ignore[misc]
    elif geometry.geom_type == "MultiPolygon":
        yield from geometry.geoms  # type: ignore[union-attr]
