"""DXF drawing loading and rendering helpers."""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import ezdxf
from ezdxf.document import Drawing
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsScene


def read_dxf(path: str | Path) -> Drawing:
    """Read a DXF document from disk."""
    return ezdxf.readfile(str(path))


def convert_dwg_to_dxf(source: str | Path, destination: str | Path) -> None:
    """Convert DWG to DXF using the bundled acadrust helper."""
    executable = "openarch-dwg-helper.exe" if sys.platform == "win32" else "openarch-dwg-helper"
    if getattr(sys, "frozen", False):
        helper = Path(sys._MEIPASS) / "openarchsp" / "bin" / executable
    else:
        helper = Path(__file__).resolve().parents[2] / "dwg-helper" / "target" / "release" / executable
    if not helper.is_file():
        raise RuntimeError(
            "The DWG converter is missing. Rebuild the application with the Rust helper, "
            "or run `cargo build --manifest-path dwg-helper/Cargo.toml --release`."
        )
    try:
        result = subprocess.run(
            [str(helper), str(source), str(destination)],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("DWG conversion timed out after 30 minutes.") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"DWG converter exited with code {result.returncode}.")
    if not Path(destination).is_file():
        raise RuntimeError("DWG converter completed without creating a DXF file.")


def _xy(point: object) -> tuple[float, float]:
    return float(point[0]), -float(point[1])  # CAD y-up to Qt scene y-down


def _entity_path(entity: object) -> QPainterPath | None:
    kind = entity.dxftype()
    path = QPainterPath()

    if kind == "LINE":
        path.moveTo(*_xy(entity.dxf.start))
        path.lineTo(*_xy(entity.dxf.end))
    elif kind in ("LWPOLYLINE", "POLYLINE"):
        points = list(entity.get_points("xy")) if kind == "LWPOLYLINE" else [
            (v.dxf.location.x, v.dxf.location.y) for v in entity.vertices
        ]
        if len(points) < 2:
            return None
        path.moveTo(*_xy(points[0]))
        for point in points[1:]:
            path.lineTo(*_xy(point))
        if kind == "LWPOLYLINE" and entity.closed or kind == "POLYLINE" and entity.is_closed:
            path.closeSubpath()
    elif kind == "CIRCLE":
        center = _xy(entity.dxf.center)
        radius = float(entity.dxf.radius)
        path.addEllipse(center[0] - radius, center[1] - radius, 2 * radius, 2 * radius)
    elif kind == "ARC":
        center = _xy(entity.dxf.center)
        radius = float(entity.dxf.radius)
        start = float(entity.dxf.start_angle)
        end = float(entity.dxf.end_angle)
        sweep = (end - start) % 360.0
        steps = max(8, math.ceil(sweep / 5.0))
        for index in range(steps + 1):
            angle = math.radians(start + sweep * index / steps)
            x = float(entity.dxf.center.x) + radius * math.cos(angle)
            y = float(entity.dxf.center.y) + radius * math.sin(angle)
            if index == 0:
                path.moveTo(*_xy((x, y)))
            else:
                path.lineTo(*_xy((x, y)))
    else:
        return None
    return path


def draw_document(document: Drawing, scene: QGraphicsScene) -> tuple[int, QRectF]:
    """Render common 2D model-space entities and return count and bounds."""
    scene.clear()
    bounds = QRectF()
    rendered = 0
    pen = QPen(QColor("#89939e"))
    pen.setCosmetic(True)
    pen.setWidthF(0.0)

    for entity in document.modelspace():
        shape = _entity_path(entity)
        if shape is None:
            continue
        item = QGraphicsPathItem(shape)
        item.setPen(pen)
        scene.addItem(item)
        item_bounds = item.sceneBoundingRect()
        bounds = item_bounds if rendered == 0 else bounds.united(item_bounds)
        rendered += 1

    if rendered:
        bounds = bounds.adjusted(-1, -1, 1, 1)
        scene.setSceneRect(bounds)
    else:
        bounds = QRectF(-10, -10, 20, 20)
        scene.setSceneRect(bounds)
    return rendered, bounds
