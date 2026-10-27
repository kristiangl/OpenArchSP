# OpenArch Swept Paths

OpenArch Swept Paths is a standalone desktop tool for checking vehicle swept paths against a site drawing. The first release uses DXF as its exchange format so drawings can be opened in CADCraft without requiring a CADCraft plugin API.

## Current workflow

1. Open a DXF site drawing as a base layer.
2. Pan with the mouse and zoom with the wheel; use View → Zoom to Extents to reframe.
3. Select Draw steering path, then click path points in order. Right-click or press Enter to finish. Backspace removes the last point.
4. Export the steering path as a separate DXF, or save a new DXF containing a copy of the base drawing and the path.

The original source drawing is never overwritten. The generated path is placed on the OASP_STEERING_PATH layer.

The drawing viewer currently renders model-space LINE, LWPOLYLINE, POLYLINE, CIRCLE and ARC entities. Paper space, blocks, text and other entity types are not yet displayed.

## Development setup

Requirements: Python 3.11 or newer and a working desktop environment supported by Qt.

PowerShell:

    cd OpenArchSP
    py -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install --upgrade pip
    python -m pip install -e .
    openarchsp

On macOS or Linux, activate the environment with source .venv/bin/activate.

## Planned next

- Australian design-vehicle library with editable and source-attributed dimensions.
- Turning and articulated-vehicle kinematics, followed by swept-envelope rendering.
- DXF export of vehicle paths and envelopes.
- Optional DWG conversion support.

Austroads AP-G34-23 is the initial reference for design-vehicle definitions. Jurisdictional variants and project-specific vehicles need to remain configurable. Before design use, vehicle geometry, steering assumptions, clearances and generated paths must be validated against authoritative templates and representative cases.
