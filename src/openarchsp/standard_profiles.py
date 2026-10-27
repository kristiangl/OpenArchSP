"""Seed editable kinematic profiles from the Austroads AP-G34-23 vehicle figures.

The guide gives body, axle-station and combination dimensions, but does not
publish a road-wheel maximum angle or lateral track dimension for every axle.
Profiles therefore mark those inferred values in their source description;
they are useful starting geometries, not validated PBS models.
"""

from __future__ import annotations

from openarchsp.vehicle_kinematics import CombinationGeometry, VehicleUnitGeometry


SOURCE = (
    "Austroads (2023), AP-G34-23, Design Vehicles and Turning Path Templates Guide, "
    "Edition 4.0, vehicle figures 2.1–2.13. Longitudinal stations transcribed from "
    "the drawings; coupling offsets for multi-trailer combinations are reconstructed "
    "from the schematic dimension chains. Axle track is estimated from the schematic; "
    "35° maximum road-wheel steering is an OpenArch provisional assumption, not an "
    "AP-G34 value. Verify against the project vehicle before design use."
)


def _unit(
    name: str,
    *,
    front: float,
    rear: float,
    axles: tuple[float, ...],
    track: float = 2.0,
    front_hitch: float = 0.0,
    rear_hitch: float = 0.0,
    hitch_width: float = 0.25,
) -> VehicleUnitGeometry:
    return VehicleUnitGeometry(
        name=name,
        width_m=2.5,
        front_extent_m=front,
        rear_extent_m=rear,
        axle_offsets_m=axles,
        track_m=track,
        front_hitch_m=front_hitch,
        rear_hitch_m=rear_hitch,
        hitch_width_m=hitch_width,
    )


def _profile(
    name: str,
    units: tuple[VehicleUnitGeometry, ...],
    steer_axle_m: float,
    figure: str,
    *,
    max_articulation_deg: float = 70.0,
) -> CombinationGeometry:
    return CombinationGeometry(
        name=name,
        units=units,
        steer_axle_m=steer_axle_m,
        max_steer_deg=35.0,
        max_articulation_deg=max_articulation_deg,
        source_reference=f"{SOURCE} Figure {figure}; default articulation limit 70° per section 3.5.",
    )


def standard_vehicle_geometries() -> dict[str, CombinationGeometry]:
    """Return initial editable AP-G34 geometry for the supported design vehicles."""
    triple_13 = (1.3, 0.0, -1.3)
    triple_125 = (1.25, 0.0, -1.25)
    tandem_13 = (0.65, -0.65)

    profiles = {
        "passenger-vehicle-5-2": _profile(
            "Passenger vehicle (5.2 m)",
            (
                VehicleUnitGeometry(
                    "Passenger vehicle",
                    1.94,
                    4.25,
                    0.95,
                    (3.05, 0.0),
                    1.84,
                ),
            ),
            3.05,
            "2.1",
        ),
        "service-vehicle-8-8": _profile(
            "Service vehicle (8.8 m)",
            (
                _unit(
                    "Service vehicle",
                    front=7.3,
                    rear=1.5,
                    axles=(5.0, 0.0),
                ),
            ),
            5.0,
            "2.3",
        ),
        "single-unit-truck-bus-12-5": _profile(
            "Single unit truck / bus (12.5 m)",
            (
                _unit(
                    "Truck / bus body",
                    front=9.05,
                    rear=3.45,
                    axles=(6.85, 0.65, -0.65),
                ),
            ),
            6.85,
            "2.4",
        ),
        "long-rigid-bus-14-5": _profile(
            "Long rigid bus (14.5 m)",
            (
                _unit(
                    "Long rigid bus",
                    front=11.0,
                    rear=3.5,
                    axles=(8.4, 0.65, -0.65),
                ),
            ),
            8.4,
            "2.5",
        ),
        "passenger-vehicle-towing-trailer-17-6": _profile(
            "Passenger vehicle towing trailer (17.6 m)",
            (
                VehicleUnitGeometry(
                    "Passenger vehicle",
                    1.94,
                    4.25,
                    0.95,
                    (3.05, 0.0),
                    1.84,
                    rear_hitch_m=1.15,
                ),
                VehicleUnitGeometry(
                    "Caravan / trailer",
                    2.5,
                    8.5,
                    3.7,
                    (0.5, -0.5),
                    2.0,
                    front_hitch_m=8.5,
                ),
            ),
            3.05,
            "2.2",
        ),
        "prime-mover-semi-trailer-19": _profile(
            "Prime mover and semi-trailer (19 m)",
            (
                _unit(
                    "Prime mover",
                    front=6.9,
                    rear=0.9,
                    axles=(5.3, *tandem_13),
                    rear_hitch=-0.2,
                ),
                _unit(
                    "Semi-trailer",
                    front=9.5,
                    rear=2.8,
                    axles=triple_13,
                    front_hitch=9.5,
                ),
            ),
            5.3,
            "2.7",
        ),
        "prime-mover-long-semi-trailer-25": _profile(
            "Prime mover and long semi-trailer (25 m)",
            (
                _unit(
                    "Prime mover",
                    front=7.0,
                    rear=0.9,
                    axles=(5.4, *tandem_13),
                    rear_hitch=-0.4,
                ),
                _unit(
                    "Long semi-trailer",
                    front=13.9,
                    rear=4.5,
                    axles=triple_13,
                    front_hitch=13.9,
                ),
            ),
            5.4,
            "2.8",
        ),
        "b-double-25": _profile(
            "B-double (25 m)",
            (
                _unit(
                    "Prime mover",
                    front=5.0,
                    rear=0.9,
                    axles=(4.0, *tandem_13),
                    rear_hitch=-0.4,
                ),
                _unit(
                    "Lead semi-trailer",
                    front=7.9,
                    rear=1.3,
                    axles=triple_13,
                    front_hitch=7.9,
                    rear_hitch=0.2,
                ),
                _unit(
                    "Rear semi-trailer",
                    front=9.4,
                    rear=2.9,
                    axles=triple_13,
                    front_hitch=9.4,
                ),
            ),
            4.0,
            "2.9",
        ),
        "b-double-26": _profile(
            "B-double (26 m)",
            (
                _unit(
                    "Prime mover",
                    front=5.9,
                    rear=0.9,
                    axles=(4.5, *tandem_13),
                ),
                _unit(
                    "Lead semi-trailer",
                    front=7.6,
                    rear=1.3,
                    axles=triple_13,
                    front_hitch=7.6,
                    rear_hitch=0.45,
                ),
                _unit(
                    "Rear semi-trailer",
                    front=9.15,
                    rear=2.9,
                    axles=triple_13,
                    front_hitch=9.15,
                ),
            ),
            4.5,
            "2.10",
        ),
        "b-triple-35-4": _profile(
            "B-triple (35.4 m)",
            (
                _unit(
                    "Prime mover",
                    front=6.5,
                    rear=0.9,
                    axles=(5.0, *tandem_13),
                    rear_hitch=0.37,
                ),
                _unit(
                    "Lead semi-trailer",
                    front=7.3,
                    rear=1.25,
                    axles=triple_125,
                    front_hitch=7.3,
                    rear_hitch=0.1,
                ),
                _unit(
                    "Middle semi-trailer",
                    front=7.3,
                    rear=1.25,
                    axles=triple_125,
                    front_hitch=7.3,
                    rear_hitch=0.1,
                ),
                _unit(
                    "Rear semi-trailer",
                    front=10.73,
                    rear=3.0,
                    axles=triple_125,
                    front_hitch=10.73,
                ),
            ),
            5.0,
            "2.12",
        ),
        "a-double-36-2": _profile(
            "A-double / Type I road train (36.2 m)",
            (
                _unit(
                    "Prime mover",
                    front=7.1,
                    rear=0.9,
                    axles=(5.5, *tandem_13),
                    rear_hitch=-0.3,
                ),
                _unit(
                    "Lead A-trailer",
                    front=9.5,
                    rear=1.3,
                    axles=triple_13,
                    front_hitch=9.5,
                    rear_hitch=1.2,
                ),
                _unit(
                    "Converter dolly",
                    front=1.4,
                    rear=0.7,
                    axles=tandem_13,
                    front_hitch=1.4,
                    rear_hitch=5.0,
                    hitch_width=0.3,
                ),
                _unit(
                    "Rear A-trailer",
                    front=9.5,
                    rear=2.8,
                    axles=triple_13,
                    front_hitch=9.5,
                ),
            ),
            5.5,
            "2.11",
        ),
        "a-triple-53-4": _profile(
            "A-triple / Type II road train (53.4 m)",
            (
                _unit(
                    "Prime mover",
                    front=7.1,
                    rear=0.9,
                    axles=(5.5, *tandem_13),
                    rear_hitch=-0.3,
                ),
                _unit(
                    "Lead A-trailer",
                    front=9.5,
                    rear=1.3,
                    axles=triple_13,
                    front_hitch=9.5,
                    rear_hitch=1.2,
                ),
                _unit(
                    "First converter dolly",
                    front=1.4,
                    rear=0.7,
                    axles=tandem_13,
                    front_hitch=1.4,
                    rear_hitch=5.0,
                    hitch_width=0.3,
                ),
                _unit(
                    "Middle A-trailer",
                    front=9.5,
                    rear=1.3,
                    axles=triple_13,
                    front_hitch=9.5,
                    rear_hitch=1.2,
                ),
                _unit(
                    "Second converter dolly",
                    front=1.4,
                    rear=0.7,
                    axles=tandem_13,
                    front_hitch=1.4,
                    rear_hitch=5.0,
                    hitch_width=0.3,
                ),
                _unit(
                    "Rear A-trailer",
                    front=9.6,
                    rear=2.8,
                    axles=triple_13,
                    front_hitch=9.6,
                ),
            ),
            5.5,
            "2.13",
        ),
    }
    for geometry in profiles.values():
        geometry.validate()
    return profiles
