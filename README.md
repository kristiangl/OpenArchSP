# OpenArch Swept Paths

OpenArch Swept Paths is a standalone desktop application built with Python, PySide6 (Qt) and ezdxf. The application is packaged separately on native Windows, macOS and Linux runners; PyInstaller does not cross-compile desktop binaries between operating systems.

## Downloads

- **Windows x64:** Install with the .exe setup program.
- **macOS Intel and Apple Silicon:** Open the matching .dmg and drag the app into Applications. These builds are currently unsigned and not notarized, so macOS may require you to approve the app the first time it opens.
- **Linux x86_64:** Make the .AppImage executable, then launch it. It is built on Ubuntu 22.04 and requires a compatible glibc-based Linux distribution.

On every push to main, pull request, or manual workflow run, GitHub Actions builds all four platform packages. The files are downloadable from that run's Artifacts section for 14 days. To publish permanent downloads under Releases, update the version in pyproject.toml, push a matching version tag such as v0.1.1, and wait for the workflow to finish.

The GitHub-hosted build matrix creates Windows x64, macOS Intel, macOS Apple Silicon and Linux x86_64 packages. The macOS builds are separate because PyInstaller packages for the architecture on which it runs.

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

## Swept-path roadmap

- Australian design-vehicle library with editable and source-attributed dimensions.
- Turning and articulated-vehicle kinematics, followed by swept-envelope rendering.
- DXF export of vehicle paths and envelopes.
- Optional DWG conversion support.

Austroads AP-G34-23 is the initial reference for design-vehicle definitions. Jurisdictional variants and project-specific vehicles need to remain configurable. Before design use, vehicle geometry, steering assumptions, clearances and generated paths must be validated against authoritative templates and representative cases.
