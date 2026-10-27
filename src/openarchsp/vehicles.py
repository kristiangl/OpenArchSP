"""Editable Australian design-vehicle catalogue metadata."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class VehicleDefinition:
    """Vehicle metadata used by the initial rigid-envelope preview."""

    id: str
    name: str
    length_m: float
    width_m: float
    model: str
    source: str
    source_url: str
    notes: str = ""

    @property
    def supports_preview(self) -> bool:
        return self.model == "rigid"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "VehicleDefinition":
        return cls(
            id=str(value["id"]),
            name=str(value["name"]),
            length_m=float(value["length_m"]),
            width_m=float(value["width_m"]),
            model=str(value.get("model", "rigid")),
            source=str(value.get("source", "User-defined")),
            source_url=str(value.get("source_url", "")),
            notes=str(value.get("notes", "")),
        )


AUSTROADS_SOURCE = "Austroads AP-G34-23 (2023)"
AUSTROADS_URL = "https://austroads.gov.au/publications/road-design/ap-g34"

STANDARD_VEHICLES: tuple[VehicleDefinition, ...] = (
    VehicleDefinition(
        "passenger-vehicle-5-2",
        "Passenger vehicle (5.2 m)",
        5.2,
        1.9,
        "rigid",
        AUSTROADS_SOURCE,
        AUSTROADS_URL,
        "Rigid-body preview only; reference path is the vehicle body centreline.",
    ),
    VehicleDefinition(
        "service-vehicle-8-8",
        "Service vehicle (8.8 m)",
        8.8,
        2.5,
        "rigid",
        AUSTROADS_SOURCE,
        AUSTROADS_URL,
        "Rigid-body preview only; reference path is the vehicle body centreline.",
    ),
    VehicleDefinition(
        "single-unit-truck-bus-12-5",
        "Single unit truck / bus (12.5 m)",
        12.5,
        2.5,
        "rigid",
        AUSTROADS_SOURCE,
        AUSTROADS_URL,
        "Rigid-body preview only; wheelbase and steering limits are not modelled.",
    ),
    VehicleDefinition(
        "long-rigid-bus-14-5",
        "Long rigid bus (14.5 m)",
        14.5,
        2.5,
        "rigid",
        AUSTROADS_SOURCE,
        AUSTROADS_URL,
        "Rigid-body preview only; wheelbase and steering limits are not modelled.",
    ),
    VehicleDefinition(
        "passenger-vehicle-towing-trailer-17-6",
        "Passenger vehicle towing trailer (17.6 m)",
        17.6,
        2.5,
        "articulated",
        AUSTROADS_SOURCE,
        AUSTROADS_URL,
        "Needs a tractor and trailer geometry profile before calculation.",
    ),
    VehicleDefinition(
        "articulated-bus-19",
        "Articulated bus (19 m)",
        19.0,
        2.5,
        "articulated",
        AUSTROADS_SOURCE,
        AUSTROADS_URL,
        "Needs an articulation and axle geometry profile before calculation.",
    ),
    VehicleDefinition(
        "prime-mover-semi-trailer-19",
        "Prime mover and semi-trailer (19 m)",
        19.0,
        2.5,
        "articulated",
        AUSTROADS_SOURCE,
        AUSTROADS_URL,
        "Needs a tractor and trailer geometry profile before calculation.",
    ),
    VehicleDefinition(
        "prime-mover-long-semi-trailer-25",
        "Prime mover and long semi-trailer (25 m)",
        25.0,
        2.5,
        "articulated",
        AUSTROADS_SOURCE,
        AUSTROADS_URL,
        "Needs a tractor and trailer geometry profile before calculation.",
    ),
    VehicleDefinition(
        "b-double-25",
        "B-double (25 m)",
        25.0,
        2.5,
        "articulated",
        AUSTROADS_SOURCE,
        AUSTROADS_URL,
        "Needs tractor, lead-trailer and rear-trailer geometry before calculation.",
    ),
    VehicleDefinition(
        "b-double-26",
        "B-double (26 m)",
        26.0,
        2.5,
        "articulated",
        AUSTROADS_SOURCE,
        AUSTROADS_URL,
        "Needs tractor, lead-trailer and rear-trailer geometry before calculation.",
    ),
    VehicleDefinition(
        "b-triple-35-4",
        "B-triple (35.4 m)",
        35.4,
        2.5,
        "articulated",
        AUSTROADS_SOURCE,
        AUSTROADS_URL,
        "Needs tractor and three-trailer geometry before calculation.",
    ),
    VehicleDefinition(
        "a-double-36-2",
        "A-double / Type I road train (36.2 m)",
        36.2,
        2.5,
        "articulated",
        AUSTROADS_SOURCE,
        AUSTROADS_URL,
        "Needs tractor and two-trailer geometry before calculation.",
    ),
    VehicleDefinition(
        "a-triple-53-4",
        "A-triple / Type II road train (53.4 m)",
        53.4,
        2.5,
        "articulated",
        AUSTROADS_SOURCE,
        AUSTROADS_URL,
        "Needs tractor and three-trailer geometry before calculation.",
    ),
)

CUSTOM_RIGID_ID = "custom-rigid"
CUSTOM_RIGID_VEHICLE = VehicleDefinition(
    CUSTOM_RIGID_ID,
    "Custom rigid vehicle",
    5.2,
    1.9,
    "rigid",
    "User-defined",
    "",
    "Rigid-body preview only; reference path is the vehicle body centreline.",
)
