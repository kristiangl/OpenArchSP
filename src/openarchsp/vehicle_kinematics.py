"""Low-speed, no-slip kinematics for steer-axle-guided vehicle combinations.

All dimensions are metres. Unit reference points are the centres of their
rearmost axle groups. This is a geometric low-speed model; it does not model
tyre forces, suspension, load transfer, or high-speed transient behaviour.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import asdict, dataclass
from collections.abc import Sequence

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union


Point = tuple[float, float]


@dataclass(frozen=True)
class VehicleUnitGeometry:
    """One vehicle body with axle and coupling offsets from its rear axle group."""

    name: str
    width_m: float
    front_extent_m: float
    rear_extent_m: float
    axle_offsets_m: tuple[float, ...]
    track_m: float
    front_hitch_m: float = 0.0
    rear_hitch_m: float = 0.0
    hitch_width_m: float = 0.25

    def validate(self, *, has_predecessor: bool) -> None:
        if not self.name.strip():
            raise ValueError("Every vehicle unit needs a name.")
        if min(
            self.width_m,
            self.front_extent_m,
            self.rear_extent_m,
            self.track_m,
            self.hitch_width_m,
        ) <= 0:
            raise ValueError(
                f"{self.name}: width, body extents, axle track and hitch width must be positive."
            )
        if not self.axle_offsets_m:
            raise ValueError(f"{self.name}: enter at least one axle position.")
        if any(
            offset > self.front_extent_m or offset < -self.rear_extent_m
            for offset in self.axle_offsets_m
        ):
            raise ValueError(f"{self.name}: an axle lies outside the body extents.")
        if has_predecessor and self.front_hitch_m <= 0:
            raise ValueError(f"{self.name}: enter the distance from its front coupling to its axle group.")


@dataclass(frozen=True)
class CombinationGeometry:
    """Geometry for one steerable lead unit and zero or more towed units."""

    name: str
    units: tuple[VehicleUnitGeometry, ...]
    steer_axle_m: float
    max_steer_deg: float = 35.0
    max_articulation_deg: float = 70.0
    source_reference: str = "User-defined; verify dimensions"

    @property
    def overall_length_m(self) -> float:
        references = [0.0]
        for index in range(1, len(self.units)):
            references.append(
                references[-1]
                - self.units[index - 1].rear_hitch_m
                - self.units[index].front_hitch_m
            )
        front_points = [
            reference + offset
            for reference, unit in zip(references, self.units)
            for offset in (unit.front_extent_m, unit.front_hitch_m, -unit.rear_hitch_m)
        ]
        rear_points = [
            reference + offset
            for reference, unit in zip(references, self.units)
            for offset in (-unit.rear_extent_m, unit.front_hitch_m, -unit.rear_hitch_m)
        ]
        front = max(front_points)
        rear = min(rear_points)
        return front - rear

    @property
    def overall_width_m(self) -> float:
        return max(unit.width_m for unit in self.units)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "CombinationGeometry":
        raw_units = value.get("units")
        if not isinstance(raw_units, list) or not raw_units:
            raise ValueError("Geometry JSON must contain a non-empty 'units' list.")
        units = tuple(
            VehicleUnitGeometry(
                name=str(raw["name"]),
                width_m=float(raw["width_m"]),
                front_extent_m=float(raw["front_extent_m"]),
                rear_extent_m=float(raw["rear_extent_m"]),
                axle_offsets_m=tuple(float(offset) for offset in raw["axle_offsets_m"]),
                track_m=float(raw["track_m"]),
                front_hitch_m=float(raw.get("front_hitch_m", 0.0)),
                rear_hitch_m=float(raw.get("rear_hitch_m", 0.0)),
                hitch_width_m=float(raw.get("hitch_width_m", 0.25)),
            )
            for raw in raw_units
        )
        result = cls(
            name=str(value.get("name", "Vehicle combination")),
            units=units,
            steer_axle_m=float(value["steer_axle_m"]),
            max_steer_deg=float(value.get("max_steer_deg", 35.0)),
            max_articulation_deg=float(value.get("max_articulation_deg", 70.0)),
            source_reference=str(value.get("source_reference", "User-defined; verify dimensions")),
        )
        result.validate()
        return result

    def validate(self) -> None:
        if not self.units:
            raise ValueError("A vehicle combination must have at least one unit.")
        if self.steer_axle_m <= 0 or self.steer_axle_m >= self.units[0].front_extent_m:
            raise ValueError("The lead steer axle must be ahead of its rear axle and inside the body.")
        if not 0 < self.max_steer_deg < 90:
            raise ValueError("Maximum steering angle must be between 0 and 90 degrees.")
        if not 0 < self.max_articulation_deg < 180:
            raise ValueError("Maximum articulation angle must be between 0 and 180 degrees.")
        for index, unit in enumerate(self.units):
            unit.validate(has_predecessor=index > 0)


@dataclass(frozen=True)
class RouteSample:
    point: Point
    heading_rad: float
    curvature_per_m: float
    distance_m: float


@dataclass(frozen=True)
class UnitTrajectory:
    name: str
    reference_points: tuple[Point, ...]
    headings_rad: tuple[float, ...]
    axle_traces: dict[str, tuple[Point, ...]]
    wheel_traces: dict[str, tuple[Point, ...]]
    articulation_deg: tuple[float, ...]


@dataclass(frozen=True)
class SimulationResult:
    envelope: BaseGeometry
    steer_axle_path: tuple[Point, ...]
    target_steer_axle_path: tuple[Point, ...]
    steering_angles_deg: tuple[float, ...]
    units: tuple[UnitTrajectory, ...]
    offtracking_by_axle_m: dict[str, float]
    maximum_articulation_deg: float
    path_length_m: float
    front_wheel_angles_deg: tuple[tuple[float, float], ...]
    max_steering_rate_deg_s: float
    allowed_steering_rate_deg_s: float
    steering_rate_saturated: bool
    maximum_path_error_m: float
    path_complete: bool


def _unit(angle: float) -> Point:
    return math.cos(angle), math.sin(angle)


def _normal(angle: float) -> Point:
    return -math.sin(angle), math.cos(angle)


def _add(a: Point, b: Point) -> Point:
    return a[0] + b[0], a[1] + b[1]


def _scale(vector: Point, factor: float) -> Point:
    return vector[0] * factor, vector[1] * factor


def _sub(a: Point, b: Point) -> Point:
    return a[0] - b[0], a[1] - b[1]


def _dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _append_sample(
    samples: list[RouteSample], point: Point, heading: float, curvature: float
) -> None:
    if samples:
        distance = math.dist(samples[-1].point, point)
        if distance <= 1e-9:
            return
        total = samples[-1].distance_m + distance
    else:
        total = 0.0
    samples.append(RouteSample(point, heading, curvature, total))


def smooth_steer_axle_path(
    route: Sequence[Point], corner_radius_m: float, spacing_m: float
) -> tuple[RouteSample, ...]:
    """Round clicked waypoints with tangent circular arcs and sample the path."""
    if corner_radius_m <= 0 or spacing_m <= 0:
        raise ValueError("Corner radius and sampling spacing must be positive.")
    points: list[Point] = []
    for point in route:
        normalized = float(point[0]), float(point[1])
        if not points or math.dist(points[-1], normalized) > 1e-8:
            points.append(normalized)
    if len(points) < 2:
        raise ValueError("Draw a path with at least two distinct points.")

    directions: list[Point] = []
    segment_lengths: list[float] = []
    for start, end in zip(points, points[1:]):
        delta = _sub(end, start)
        length = math.hypot(*delta)
        segment_lengths.append(length)
        directions.append((delta[0] / length, delta[1] / length))

    corner_data: dict[int, tuple[float, float, float, Point, Point]] = {}
    setbacks: dict[int, float] = {}
    for index in range(1, len(points) - 1):
        incoming, outgoing = directions[index - 1], directions[index]
        turn = math.atan2(
            incoming[0] * outgoing[1] - incoming[1] * outgoing[0],
            _dot(incoming, outgoing),
        )
        if abs(turn) < 1e-6:
            continue
        if abs(abs(turn) - math.pi) < math.radians(2):
            raise ValueError("A near-reversal waypoint cannot be rounded as a tangent turn.")
        tangent = math.tan(abs(turn) / 2)
        setback = corner_radius_m * tangent
        setbacks[index] = setback
        corner_data[index] = (turn, tangent, setback, incoming, outgoing)

    # Reduce neighbouring fillets only where their tangent points would overlap.
    corner_scale = {index: 1.0 for index in setbacks}
    for _ in range(2):
        for segment_index, length in enumerate(segment_lengths):
            first = segment_index if segment_index in setbacks else None
            second = segment_index + 1 if segment_index + 1 in setbacks else None
            used = (setbacks.get(first, 0.0) * corner_scale.get(first, 1.0)) + (
                setbacks.get(second, 0.0) * corner_scale.get(second, 1.0)
            )
            if used > length * 0.98:
                factor = (length * 0.98) / used
                if first is not None:
                    corner_scale[first] *= factor
                if second is not None:
                    corner_scale[second] *= factor

    tangent_in: dict[int, Point] = {}
    tangent_out: dict[int, Point] = {}
    arcs: dict[int, tuple[Point, float, float, float]] = {}
    for index, (turn, tangent, setback, incoming, _outgoing) in corner_data.items():
        effective_setback = setback * corner_scale[index]
        radius = effective_setback / tangent
        entry = _sub(points[index], _scale(incoming, effective_setback))
        exit_point = _add(points[index], _scale(directions[index], effective_setback))
        side = 1.0 if turn > 0 else -1.0
        center = _add(entry, _scale(_normal(math.atan2(incoming[1], incoming[0])), side * radius))
        start_angle = math.atan2(entry[1] - center[1], entry[0] - center[0])
        tangent_in[index] = entry
        tangent_out[index] = exit_point
        arcs[index] = center, start_angle, turn, radius

    samples: list[RouteSample] = []
    cursor = points[0]
    _append_sample(
        samples,
        cursor,
        math.atan2(directions[0][1], directions[0][0]),
        0.0,
    )
    for corner_index in range(1, len(points) - 1):
        entry = tangent_in.get(corner_index, points[corner_index])
        line_heading = math.atan2(
            entry[1] - cursor[1], entry[0] - cursor[0]
        ) if math.dist(cursor, entry) > 1e-9 else math.atan2(
            directions[corner_index - 1][1], directions[corner_index - 1][0]
        )
        length = math.dist(cursor, entry)
        steps = max(1, math.ceil(length / spacing_m))
        for step in range(1, steps + 1):
            fraction = step / steps
            point = (cursor[0] + (entry[0] - cursor[0]) * fraction,
                     cursor[1] + (entry[1] - cursor[1]) * fraction)
            _append_sample(samples, point, line_heading, 0.0)

        if corner_index in arcs:
            center, start_angle, turn, radius = arcs[corner_index]
            arc_length = abs(turn) * radius
            steps = max(1, math.ceil(arc_length / spacing_m))
            side = 1.0 if turn > 0 else -1.0
            for step in range(1, steps + 1):
                angle = start_angle + turn * step / steps
                point = (center[0] + radius * math.cos(angle),
                         center[1] + radius * math.sin(angle))
                _append_sample(samples, point, angle + side * math.pi / 2, side / radius)
        cursor = tangent_out.get(corner_index, points[corner_index])

    final_point = points[-1]
    heading = math.atan2(directions[-1][1], directions[-1][0])
    length = math.dist(cursor, final_point)
    steps = max(1, math.ceil(length / spacing_m))
    for step in range(1, steps + 1):
        fraction = step / steps
        point = (cursor[0] + (final_point[0] - cursor[0]) * fraction,
                 cursor[1] + (final_point[1] - cursor[1]) * fraction)
        _append_sample(samples, point, heading, 0.0)

    if len(samples) < 2:
        raise ValueError("The rounded steer-axle path has no measurable length.")
    return tuple(samples)


def _wrap_angle(angle: float) -> float:
    return (angle + math.pi) % (2 * math.pi) - math.pi


def _project_onto_path(
    point: Point, samples: Sequence[RouteSample], start_segment: int
) -> tuple[Point, float, int, float]:
    """Project onto a forward window of path segments for monotonic driver tracking."""
    first = max(0, start_segment - 2)
    last = min(len(samples) - 1, start_segment + 64)
    best_point = samples[first].point
    best_distance = math.inf
    best_progress = samples[first].distance_m
    best_segment = first
    for index in range(first, last):
        start, end = samples[index].point, samples[index + 1].point
        delta = _sub(end, start)
        length_squared = _dot(delta, delta)
        fraction = min(1.0, max(0.0, _dot(_sub(point, start), delta) / length_squared))
        candidate = _add(start, _scale(delta, fraction))
        distance = math.dist(candidate, point)
        if distance < best_distance:
            best_point = candidate
            best_distance = distance
            best_progress = samples[index].distance_m + fraction * (
                samples[index + 1].distance_m - samples[index].distance_m
            )
            best_segment = index
    return best_point, best_progress, best_segment, best_distance


def _point_at_distance(
    distance: float,
    samples: Sequence[RouteSample],
    distances: Sequence[float],
) -> Point:
    if distance <= 0:
        return samples[0].point
    if distance >= distances[-1]:
        return samples[-1].point
    index = min(len(samples) - 2, bisect_right(distances, distance) - 1)
    start, end = samples[index], samples[index + 1]
    span = end.distance_m - start.distance_m
    fraction = (distance - start.distance_m) / span
    return (
        start.point[0] + (end.point[0] - start.point[0]) * fraction,
        start.point[1] + (end.point[1] - start.point[1]) * fraction,
    )


def _rectangle(reference: Point, heading: float, unit: VehicleUnitGeometry) -> Polygon:
    forward = _unit(heading)
    side = _normal(heading)
    corners: list[Point] = []
    for longitudinal, lateral in (
        (unit.front_extent_m, unit.width_m / 2),
        (unit.front_extent_m, -unit.width_m / 2),
        (-unit.rear_extent_m, -unit.width_m / 2),
        (-unit.rear_extent_m, unit.width_m / 2),
    ):
        corners.append(_add(reference, _add(_scale(forward, longitudinal), _scale(side, lateral))))
    return Polygon(corners)


def _coupling_strip(
    reference: Point, heading: float, start_m: float, end_m: float, width_m: float
) -> Polygon:
    forward = _unit(heading)
    side = _normal(heading)
    start = _add(reference, _scale(forward, start_m))
    end = _add(reference, _scale(forward, end_m))
    half_width = width_m / 2
    return Polygon(
        (
            _add(start, _scale(side, half_width)),
            _sub(start, _scale(side, half_width)),
            _sub(end, _scale(side, half_width)),
            _add(end, _scale(side, half_width)),
        )
    )


def simulate_combination(
    route: Sequence[Point],
    geometry: CombinationGeometry,
    *,
    corner_radius_m: float = 12.5,
    spacing_m: float = 0.25,
    speed_kmh: float = 5.0,
    lock_to_lock_s: float = 6.0,
    max_poses: int = 60_000,
) -> SimulationResult:
    """Simulate a forward combination with rate-limited steer-axle path following.

    The lead unit uses a bicycle model and pure-pursuit driver. Its road-wheel
    steering angle changes at the configured lock-to-lock rate, so the actual
    steer-axle path can deviate from the rounded target path. Towed units follow
    their front couplings with a no-lateral-slip rear axle constraint.
    """
    geometry.validate()
    if speed_kmh <= 0 or lock_to_lock_s <= 0:
        raise ValueError("Speed and steering lock-to-lock time must be positive.")
    target_samples = smooth_steer_axle_path(route, corner_radius_m, spacing_m)
    if len(target_samples) > max_poses:
        raise ValueError("This path exceeds the simulation pose limit; increase sampling spacing.")

    lead = geometry.units[0]
    speed = speed_kmh / 3.6
    dt = min(0.05, spacing_m / speed)
    maximum_steer = math.radians(geometry.max_steer_deg)
    maximum_steer_rate = 2 * maximum_steer / lock_to_lock_s
    lookahead = max(2.0, 1.5 * geometry.steer_axle_m)
    target_distances = [sample.distance_m for sample in target_samples]
    total_target_length = target_distances[-1]

    heading = target_samples[0].heading_rad
    steering_angle = 0.0
    rear_axle = _sub(target_samples[0].point, _scale(_unit(heading), geometry.steer_axle_m))
    lead_poses: list[tuple[Point, float]] = [(rear_axle, heading)]
    steering: list[float] = [steering_angle]
    steer_points: list[Point] = [target_samples[0].point]
    actual_distances: list[float] = [0.0]
    progress = 0.0
    path_segment = 0
    maximum_path_error = 0.0
    path_complete = False
    steering_rate_saturated = False
    maximum_duration = max(60.0, total_target_length / speed * 4 + 30.0)
    maximum_steps = min(max_poses - 1, math.ceil(maximum_duration / dt))

    for _step in range(maximum_steps):
        front = _add(rear_axle, _scale(_unit(heading), geometry.steer_axle_m))
        _projected, projected_s, path_segment, path_error = _project_onto_path(
            front, target_samples, path_segment
        )
        progress = max(progress, projected_s)
        maximum_path_error = max(maximum_path_error, path_error)
        distance_to_end = math.dist(front, target_samples[-1].point)
        if progress >= total_target_length - spacing_m and distance_to_end <= max(0.5, 2 * spacing_m):
            path_complete = True
            break

        target_s = min(total_target_length, progress + lookahead)
        aim = _point_at_distance(target_s, target_samples, target_distances)
        chord = _sub(aim, front)
        chord_length = max(math.hypot(*chord), 0.1)
        front_course = heading + steering_angle
        aim_angle = math.atan2(chord[1], chord[0])
        heading_error = _wrap_angle(aim_angle - front_course)
        desired_curvature = 2 * math.sin(heading_error) / chord_length
        curvature_rate = speed / max(math.cos(steering_angle), 1e-4) * (
            desired_curvature - math.sin(steering_angle) / geometry.steer_axle_m
        )
        if abs(curvature_rate) > maximum_steer_rate:
            steering_rate_saturated = True
        steering_rate = min(max(curvature_rate, -maximum_steer_rate), maximum_steer_rate)
        next_steering = min(
            maximum_steer,
            max(-maximum_steer, steering_angle + steering_rate * dt),
        )

        mid_steering = (steering_angle + next_steering) / 2
        yaw_rate = speed / geometry.steer_axle_m * math.tan(mid_steering)
        mid_heading = heading + yaw_rate * dt / 2
        next_rear_axle = _add(rear_axle, _scale(_unit(mid_heading), speed * dt))
        next_heading = heading + yaw_rate * dt
        next_front = _add(next_rear_axle, _scale(_unit(next_heading), geometry.steer_axle_m))
        actual_distances.append(actual_distances[-1] + math.dist(steer_points[-1], next_front))
        steer_points.append(next_front)
        lead_poses.append((next_rear_axle, next_heading))
        steering.append(next_steering)
        rear_axle, heading, steering_angle = next_rear_axle, next_heading, next_steering

    samples = tuple(
        RouteSample(point, body_heading + angle, 0.0, distance)
        for point, (_rear, body_heading), angle, distance in zip(
            steer_points, lead_poses, steering, actual_distances
        )
    )
    if len(samples) > max_poses:
        raise ValueError("This path exceeds the simulation pose limit; increase sampling spacing.")

    front_wheel_angles: list[tuple[float, float]] = []
    for angle in steering:
        if abs(angle) < 1e-9:
            front_wheel_angles.append((0.0, 0.0))
            continue
        rear_radius = geometry.steer_axle_m / abs(math.tan(angle))
        inner_radius = max(0.01, rear_radius - lead.track_m / 2)
        inner = math.atan(geometry.steer_axle_m / inner_radius)
        outer = math.atan(
            geometry.steer_axle_m / (rear_radius + lead.track_m / 2)
        )
        turn_sign = 1.0 if angle > 0 else -1.0
        left_angle, right_angle = (
            (turn_sign * inner, turn_sign * outer)
            if turn_sign > 0
            else (turn_sign * outer, turn_sign * inner)
        )
        front_wheel_angles.append((math.degrees(left_angle), math.degrees(right_angle)))

    max_steer_rate = max(
        (
            abs(steering[index + 1] - steering[index])
            / dt
            * 180
            / math.pi
            for index in range(len(steering) - 1)
        ),
        default=0.0,
    )
    allowed_steer_rate = math.degrees(maximum_steer_rate)
    all_poses: list[list[tuple[Point, float]]] = [lead_poses]
    articulation_values: list[list[float]] = [[0.0] * len(samples)]

    for unit_index in range(1, len(geometry.units)):
        towing_unit = geometry.units[unit_index - 1]
        trailer = geometry.units[unit_index]
        towing_poses = all_poses[-1]
        hitch_distance = trailer.front_hitch_m
        hitches = [
            _sub(reference, _scale(_unit(heading), towing_unit.rear_hitch_m))
            for reference, heading in towing_poses
        ]
        trailer_headings = [towing_poses[0][1]]
        trailer_references = [
            _sub(hitches[0], _scale(_unit(trailer_headings[0]), hitch_distance))
        ]
        for sample_index in range(len(samples) - 1):
            ds = samples[sample_index + 1].distance_m - samples[sample_index].distance_m
            hitch_velocity = _scale(_sub(hitches[sample_index + 1], hitches[sample_index]), 1 / ds)
            heading = trailer_headings[-1]

            def heading_derivative(value: float) -> float:
                return _dot(hitch_velocity, _normal(value)) / hitch_distance

            k1 = heading_derivative(heading)
            k2 = heading_derivative(heading + ds * k1 / 2)
            k3 = heading_derivative(heading + ds * k2 / 2)
            k4 = heading_derivative(heading + ds * k3)
            next_heading = heading + ds * (k1 + 2 * k2 + 2 * k3 + k4) / 6
            articulation = (next_heading - towing_poses[sample_index + 1][1] + math.pi) % (
                2 * math.pi
            ) - math.pi
            if abs(articulation) > math.radians(geometry.max_articulation_deg):
                raise ValueError(
                    f"{trailer.name} exceeds the {geometry.max_articulation_deg:.0f}° "
                    "maximum articulation limit. Change the path or the vehicle geometry."
                )
            trailer_headings.append(next_heading)
            trailer_references.append(
                _sub(hitches[sample_index + 1], _scale(_unit(next_heading), hitch_distance))
            )
        relative_angles = [
            (heading - towing[1] + math.pi) % (2 * math.pi) - math.pi
            for heading, towing in zip(trailer_headings, towing_poses)
        ]
        all_poses.append(list(zip(trailer_references, trailer_headings)))
        articulation_values.append([math.degrees(value) for value in relative_angles])

    polygons: list[Polygon] = []
    trajectories: list[UnitTrajectory] = []
    offtracking: dict[str, float] = {}
    for unit_index, (unit, poses) in enumerate(zip(geometry.units, all_poses)):
        references = tuple(reference for reference, _heading in poses)
        headings = tuple(heading for _reference, heading in poses)
        axle_traces: dict[str, tuple[Point, ...]] = {}
        wheel_traces: dict[str, tuple[Point, ...]] = {}
        for axle_index, offset in enumerate(unit.axle_offsets_m, start=1):
            key = f"{unit.name} axle {axle_index}"
            axle_points: list[Point] = []
            left_wheels: list[Point] = []
            right_wheels: list[Point] = []
            for (reference, heading), sample in zip(poses, samples):
                axle_center = _add(reference, _scale(_unit(heading), offset))
                axle_points.append(axle_center)
                normal = _normal(heading)
                left_wheels.append(_add(axle_center, _scale(normal, unit.track_m / 2)))
                right_wheels.append(_sub(axle_center, _scale(normal, unit.track_m / 2)))
                relative = _dot(_sub(axle_center, sample.point), _normal(sample.heading_rad))
                offtracking[key] = max(offtracking.get(key, 0.0), abs(relative))
            axle_traces[key] = tuple(axle_points)
            wheel_traces[f"{key} left wheel"] = tuple(left_wheels)
            wheel_traces[f"{key} right wheel"] = tuple(right_wheels)
        for reference, heading in poses:
            polygons.append(_rectangle(reference, heading, unit))
            front_hitch = unit.front_hitch_m
            if front_hitch > unit.front_extent_m:
                polygons.append(
                    _coupling_strip(
                        reference,
                        heading,
                        unit.front_extent_m,
                        front_hitch,
                        unit.hitch_width_m,
                    )
                )
            rear_hitch = -unit.rear_hitch_m
            if rear_hitch < -unit.rear_extent_m:
                polygons.append(
                    _coupling_strip(
                        reference,
                        heading,
                        rear_hitch,
                        -unit.rear_extent_m,
                        unit.hitch_width_m,
                    )
                )
            elif rear_hitch > unit.front_extent_m:
                polygons.append(
                    _coupling_strip(
                        reference,
                        heading,
                        unit.front_extent_m,
                        rear_hitch,
                        unit.hitch_width_m,
                    )
                )
        trajectories.append(
            UnitTrajectory(
                unit.name,
                references,
                headings,
                axle_traces,
                wheel_traces,
                tuple(articulation_values[unit_index]),
            )
        )

    envelope = unary_union(polygons)
    if envelope.is_empty or not envelope.is_valid:
        raise ValueError("Could not create a valid vehicle envelope for this path.")
    max_articulation = max(
        (abs(value) for unit in articulation_values[1:] for value in unit), default=0.0
    )
    return SimulationResult(
        envelope,
        tuple(sample.point for sample in samples),
        tuple(sample.point for sample in target_samples),
        tuple(math.degrees(value) for value in steering),
        tuple(trajectories),
        offtracking,
        max_articulation,
        samples[-1].distance_m,
        tuple(front_wheel_angles),
        max_steer_rate,
        allowed_steer_rate,
        steering_rate_saturated,
        maximum_path_error,
        path_complete,
    )
