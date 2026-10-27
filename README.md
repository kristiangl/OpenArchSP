# OpenArch Swept Paths

<p align="center"><img src="docs/assets/openarchsp-logo.svg" alt="OpenArchSP" width="640"></p>

OpenArch Swept Paths is a standalone desktop application built with Python, PySide6 (Qt), ezdxf and Shapely, with a small Rust helper for DWG conversion. The app imports DWG and DXF drawings and exports DXF. GitHub Actions packages it on native Windows, macOS and Linux runners.

## Downloads

Open the repository’s Actions page and choose the latest successful Build installers run. Download the artifact for your platform and unzip it:

- **Windows x64:** Run the OpenArch Swept Paths Setup executable.
- **macOS Intel or Apple Silicon:** Open the matching disk image and drag the app into Applications. Builds are unsigned and not notarized, so macOS may require approval on first launch.
- **Linux x86_64:** Make the AppImage executable and launch it. It is built on Ubuntu 22.04 and targets compatible glibc-based distributions.

Actions artifacts are retained for 14 days. A matching version tag publishes permanent downloads under Releases. Bump the version in pyproject.toml and push a matching tag, for example v0.1.1.

## Current workflow

1. Open a DWG or DXF site drawing as a base layer. DWG files are converted to a temporary DXF for processing; the original DWG is never modified.
2. Pan with the mouse and zoom with the wheel; use View → Zoom to Extents to reframe.
3. Choose a design vehicle and check or edit its overall length and width.
4. Draw a body-centre reference path by clicking points; right-click or press Enter to finish. Backspace undoes the last point.
5. For rigid vehicles, calculate a sampled rectangular footprint preview. Export the path and preview as a separate DXF or save them into a copy of the original drawing.

The source drawing is never overwritten. Saving a copy of a DWG-based project creates a DXF copy containing the converted base drawing and overlays. The reference path is saved on OASP_STEERING_PATH and the preview boundary on OASP_SWEPT_ENVELOPE_PREVIEW. DXF units are read when available; unitless files default to metres and expose a drawing-units-per-metre control. DWG conversion uses bundled acadrust (MPL-2.0); see THIRD_PARTY_NOTICES.md. Conversion may not preserve every proprietary/custom object, and the viewer only displays its supported model-space entity types.

The initial Austroads catalogue includes passenger vehicle, service vehicle, truck/bus, semi-trailer, B-double, B-triple, A-double and A-triple definitions. Overall lengths are attributed to AP-G34-23. User-defined rigid vehicle profiles are saved locally between runs.

## Model limits

The current calculator is a rigid rectangle following the drawn body-centre path. It is a visual preview, not a full vehicle dynamics or design-vehicle simulation: it does not calculate steering limits, wheel paths, axle off-tracking or articulated trailer motion. Articulated vehicles are listed but calculation is disabled until their tractor, axle, trailer and coupling geometry is configured and validated.

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

## Roadmap

- Accurate articulated kinematics and geometry for semi-trailers, B-doubles, B-triples and road trains.
- Turning input and realistic steering constraints.
- Validation against authoritative Austroads and jurisdictional turning templates.
- Broader CAD entity rendering and DWG conversion fidelity.
