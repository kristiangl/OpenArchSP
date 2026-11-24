"""Application entry point for OpenArch Swept Paths."""

from __future__ import annotations

import json
import datetime
import math
import re
import sys
import tempfile
import uuid
from collections.abc import Callable, Sequence
from dataclasses import fields, replace
from html import escape
from urllib.parse import urlsplit
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import ezdxf
from PySide6.QtCore import QObject, QRunnable, QSettings, QSize, QThreadPool, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QDesktopServices,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QFont,
    QPen,
    QUndoStack,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStatusBar,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QScrollArea,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
    QHBoxLayout,
)

from openarchsp.start_screen import StartScreen
from openarchsp.analysis import check_envelope_clearance, modelspace_layers
from openarchsp.history import EditCommand, EditorState, PathState, undoable
from openarchsp.drawing import make_scene, prepare_background, read_dxf
from openarchsp.project import PathSettings, Project, SweptPathDocument, calculate_path, save_project
from openarchsp.swept_path import polygons
from openarchsp import __version__
from openarchsp.standard_profiles import standard_vehicle_geometries
from openarchsp.vehicles import (
    CUSTOM_RIGID_VEHICLE,
    STANDARD_VEHICLES,
    VehicleDefinition,
)
from openarchsp.vehicle_kinematics import CombinationGeometry, SimulationResult


def asset_path(name: str) -> Path:
    """Resolve a packaged brand asset in development and frozen builds."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "openarchsp" / "assets" / name
    return Path(__file__).resolve().parent / "assets" / name


def action_icon(name: str) -> QIcon:
    icon_file = asset_path(f"icons/{name}.svg")
    # Importing QtSvg makes PyInstaller include the SVG image plugin used by QIcon.
    if not QSvgRenderer(str(icon_file)).isValid():
        return QIcon()
    return QIcon(str(icon_file))


def command_button(label, icon, parent=None):
    button = QToolButton(parent)
    button.setText(label)
    button.setIcon(action_icon(icon))
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
    button.setAccessibleName(label)
    return button


class DrawingView(QGraphicsView):
    worldClicked = Signal(float, float)
    positionClicked = Signal(float, float)
    worldMoved = Signal(float, float)
    finishRequested = Signal()
    insertWaypointRequested = Signal(float, float)
    deleteWaypointsRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.drawing_mode = False
        self.position_mode = False
        self.insert_waypoint_mode = False
        self._pan_position = None
        self.scene = QGraphicsScene(self)
        self.drawing_bounds = None
        self.setScene(self.scene)
        self.setBackgroundBrush(QColor("#1b1d20"))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMouseTracking(True)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

    def fit_drawing(self) -> None:
        if self.scene.items():
            bounds = self.drawing_bounds or self.scene.sceneRect()
            self.fitInView(bounds, Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event: object) -> None:
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.scale(factor, factor)

    def mousePressEvent(self, event: object) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_position = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if self.drawing_mode and event.button() == Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            self.worldClicked.emit(point.x(), -point.y())
            event.accept()
            return
        if self.position_mode and event.button() == Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            self.positionClicked.emit(point.x(), -point.y())
            event.accept()
            return
        if self.insert_waypoint_mode and event.button() == Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            self.insertWaypointRequested.emit(point.x(), -point.y())
            self.insert_waypoint_mode = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        if self.drawing_mode and event.button() == Qt.MouseButton.RightButton:
            self.finishRequested.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: object) -> None:
        if self.drawing_mode and event.button() == Qt.MouseButton.LeftButton:
            # Qt already sent the first press, which committed the final waypoint.
            self.finishRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: object) -> None:
        if self.drawing_mode and self._pan_position is None:
            point = self.mapToScene(event.position().toPoint())
            self.worldMoved.emit(point.x(), -point.y())
        if self._pan_position is not None:
            current = event.position().toPoint()
            delta = current - self._pan_position
            self._pan_position = current
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: object) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._pan_position is not None:
            self._pan_position = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: object) -> None:
        if event.key() == Qt.Key.Key_Delete:
            self.deleteWaypointsRequested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self.insert_waypoint_mode:
            self.insert_waypoint_mode = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self.drawing_mode:
            self.finishRequested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self.position_mode:
            self.finishRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class _PreviewSignals(QObject):
    finished = Signal(int, object, str)


class _PreviewWorker(QRunnable):
    """Calculate one drawing preview without blocking mouse interaction."""

    def __init__(self, token, route, geometry, settings, units, fast):
        super().__init__()
        self.token = token
        self.arguments = route, geometry, settings, units, fast
        self.signals = _PreviewSignals()

    def run(self) -> None:
        try:
            payload = calculate_path(*self.arguments)
            self.signals.finished.emit(self.token, payload, "")
        except Exception as error:
            self.signals.finished.emit(self.token, None, str(error))


class _ClearanceSignals(QObject):
    finished = Signal(object, str)


class _ClearanceWorker(QRunnable):
    def __init__(self, envelope, document, layers, scale):
        super().__init__()
        self.envelope, self.document, self.layers, self.scale = envelope, document, layers, scale
        self.signals = _ClearanceSignals()

    def run(self):
        try:
            self.signals.finished.emit(
                check_envelope_clearance(self.envelope, self.document, self.layers, self.scale), ""
            )
        except Exception as error:
            self.signals.finished.emit(None, str(error))


class _UpdateSignals(QObject):
    finished = Signal(object, str)


class _UpdateCheckWorker(QRunnable):
    def __init__(self):
        super().__init__()
        self.signals = _UpdateSignals()

    def run(self):
        request = Request(
            "https://api.github.com/repos/kristiangl/OpenArchSP/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "OpenArch-Swept-Paths-Updater"},
        )
        try:
            with urlopen(request, timeout=8) as response:
                release = json.loads(response.read().decode("utf-8"))
            if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
                raise ValueError("GitHub did not return a stable release.")
            if not release.get("tag_name") or not release.get("html_url"):
                raise ValueError("The latest release is missing its version or page URL.")
            self.signals.finished.emit(release, "")
        except HTTPError as error:
            self.signals.finished.emit(None, "No published release was found yet." if error.code == 404 else str(error))
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as error:
            self.signals.finished.emit(None, str(error))


class _LoadSignals(QObject):
    progress = Signal(str, int)
    finished = Signal(object, str)


class _LoadWorker(QRunnable):
    def __init__(self, source: Path, directory: Path):
        super().__init__()
        self.source = source
        self.directory = directory
        self.signals = _LoadSignals()

    def run(self):
        try:
            payload = prepare_background(self.source, self.directory, self.signals.progress.emit)
            self.signals.finished.emit(payload, "")
        except Exception as error:
            self.signals.finished.emit(None, str(error))


class LoadingDialog(QDialog):
    def __init__(self, parent, source):
        super().__init__(parent)
        self.setWindowTitle("Open drawing or project")
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        title = QLabel(source.name)
        title.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(title)
        self.stage = QLabel("Opening file…")
        layout.addWidget(self.stage)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        layout.addWidget(self.bar)
        self.payload = None
        self.error = ""

    def update_stage(self, label, percent):
        self.stage.setText(label)
        self.bar.setRange(0, 0 if percent < 0 else 100)
        if percent >= 0:
            self.bar.setValue(percent)

    def finish(self, payload, error):
        self.payload = payload
        self.error = error
        self.accept()

    def reject(self):
        # The converter cannot be interrupted safely halfway through an import.
        pass


class WaypointItem(QGraphicsEllipseItem):
    """Screen-sized draggable route handle."""

    def __init__(
        self,
        index: int,
        point: tuple[float, float],
        color: str,
        moved_callback: Callable[[int, float, float, bool], None],
    ) -> None:
        super().__init__(-7, -7, 14, 14)
        self.index = index
        self.moved_callback = moved_callback
        self.setPos(point[0], -point[1])
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setBrush(QColor(color))
        pen = QPen(QColor("#e7eaed"))
        pen.setCosmetic(True)
        pen.setWidthF(1.4)
        self.setPen(pen)
        self.setZValue(101)
        self.setToolTip(f"Waypoint {index + 1} · drag to move")

    def mouseReleaseEvent(self, event: object) -> None:
        super().mouseReleaseEvent(event)
        index, x, y = self.index, self.pos().x(), -self.pos().y()
        QTimer.singleShot(0, lambda: self.moved_callback(index, x, y, False))

    def mouseMoveEvent(self, event: object) -> None:
        super().mouseMoveEvent(event)
        self.moved_callback(self.index, self.pos().x(), -self.pos().y(), True)


class GeometryDialog(QDialog):
    """Edit a tractor/trailer assembly and its axle and coupling stations."""

    EXAMPLE = {
        "name": "Example combination (edit to match the actual vehicle)",
        "steer_axle_m": 3.8,
        "max_steer_deg": 35.0,
        "max_articulation_deg": 70.0,
        "source_reference": "Illustrative example only; replace with verified vehicle data",
        "units": [
            {
                "name": "Prime mover",
                "width_m": 2.5,
                "front_extent_m": 5.5,
                "rear_extent_m": 0.9,
                "axle_offsets_m": [3.8, 0.65, -0.65],
                "track_m": 2.0,
                "rear_hitch_m": 0.5,
            },
            {
                "name": "Semi-trailer",
                "width_m": 2.5,
                "front_extent_m": 9.6,
                "rear_extent_m": 3.3,
                "axle_offsets_m": [1.3, 0.0, -1.3],
                "track_m": 2.0,
                "front_hitch_m": 9.6,
            },
        ],
    }

    def __init__(
        self,
        vehicle_name: str,
        existing: CombinationGeometry | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Vehicle geometry — {vehicle_name}")
        self.resize(1240, 700)
        self.geometry: CombinationGeometry | None = None
        layout = QVBoxLayout(self)
        help_label = QLabel(
            "Add the lead tractor first, then trailers and dollies in towing order. All "
            "distances are metres from each unit's rearmost axle-group centre; axle "
            "stations are comma-separated and positive forward. Front coupling is "
            "positive forward; rear coupling is positive behind the axle group and "
            "negative ahead. Check every station against the vehicle drawing."
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        data = existing.to_dict() if existing else self.EXAMPLE
        settings = QFormLayout()
        self.name_edit = QLineEdit(str(data.get("name", vehicle_name)))
        self.steer_axle_spin = self._spin(float(data.get("steer_axle_m", 3.8)), 100.0)
        self.max_steer_spin = self._spin(float(data.get("max_steer_deg", 35.0)), 89.0)
        self.max_articulation_spin = self._spin(
            float(data.get("max_articulation_deg", 70.0)), 179.0
        )
        self.source_edit = QLineEdit(str(data.get("source_reference", "User-defined")))
        settings.addRow("Combination", self.name_edit)
        settings.addRow("Lead steer axle station (m)", self.steer_axle_spin)
        settings.addRow("Maximum centre road-wheel angle (°)", self.max_steer_spin)
        settings.addRow("Maximum articulation (°)", self.max_articulation_spin)
        settings.addRow("Source / notes", self.source_edit)
        layout.addLayout(settings)

        self.units_table = QTableWidget(0, 9)
        self.units_table.setHorizontalHeaderLabels(
            (
                "Component",
                "Width (m)",
                "Front extent (m)",
                "Rear extent (m)",
                "Axle stations from group centre (m, CSV)",
                "Track (m)",
                "Front coupling (m)",
                "Rear coupling (m)",
                "Hitch width (m)",
            )
        )
        self.units_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.units_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.units_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.units_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.units_table, 1)
        raw_units = data.get("units", [])
        if isinstance(raw_units, (list, tuple)):
            for unit in raw_units:
                if isinstance(unit, dict):
                    self._append_unit(unit)

        unit_actions = QHBoxLayout()
        for label, icon, callback in (
            ("Add tractor", "add", self._add_tractor),
            ("Add trailer", "add", self._add_trailer),
            ("Add converter dolly", "add", self._add_dolly),
            ("Add axle station", "add", self._add_axle),
            ("Remove axle station", "remove", self._remove_axle),
            ("Remove component", "remove", self._remove_unit),
            ("Move up", "up", lambda: self._move_unit(-1)),
            ("Move down", "down", lambda: self._move_unit(1)),
        ):
            button = command_button(label, icon)
            button.clicked.connect(callback)
            unit_actions.addWidget(button)
        unit_actions.addStretch(1)
        layout.addLayout(unit_actions)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_geometry)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _spin(value: float, maximum: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.01, maximum)
        spin.setDecimals(2)
        spin.setSingleStep(0.1)
        spin.setValue(value)
        return spin

    def _append_unit(self, data: dict[str, object], row: int | None = None) -> None:
        row = self.units_table.rowCount() if row is None else row
        self.units_table.insertRow(row)
        values = (
            data.get("name", "Vehicle unit"),
            data.get("width_m", 2.5),
            data.get("front_extent_m", 5.0),
            data.get("rear_extent_m", 1.0),
            ", ".join(str(value) for value in data.get("axle_offsets_m", [0.0])),
            data.get("track_m", 2.0),
            data.get("front_hitch_m", 0.0),
            data.get("rear_hitch_m", 0.0),
            data.get("hitch_width_m", 0.25),
        )
        for column, value in enumerate(values):
            self.units_table.setItem(row, column, QTableWidgetItem(str(value)))

    def _add_tractor(self) -> None:
        self._append_unit(
            {
                "name": "Prime mover",
                "width_m": 2.5,
                "front_extent_m": 5.5,
                "rear_extent_m": 0.9,
                "axle_offsets_m": [3.8, 0.65, -0.65],
                "track_m": 2.0,
                "rear_hitch_m": 0.5,
            },
            0,
        )
        self.units_table.selectRow(0)

    def _add_trailer(self) -> None:
        row = self.units_table.rowCount()
        self._append_unit(
            {
                "name": f"Trailer {max(1, row)}",
                "width_m": 2.5,
                "front_extent_m": 9.6,
                "rear_extent_m": 3.3,
                "axle_offsets_m": [1.3, 0.0, -1.3],
                "track_m": 2.0,
                "front_hitch_m": 9.6,
            }
        )
        self.units_table.selectRow(self.units_table.rowCount() - 1)

    def _add_dolly(self) -> None:
        self._append_unit(
            {
                "name": "Converter dolly",
                "width_m": 2.5,
                "front_extent_m": 1.4,
                "rear_extent_m": 0.7,
                "axle_offsets_m": [0.65, -0.65],
                "track_m": 2.0,
                "front_hitch_m": 1.4,
                "rear_hitch_m": 5.0,
                "hitch_width_m": 0.3,
            }
        )
        self.units_table.selectRow(self.units_table.rowCount() - 1)

    def _add_axle(self) -> None:
        row = self.units_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Select a component", "Select a tractor, trailer or dolly first.")
            return
        station, accepted = QInputDialog.getDouble(
            self,
            "Add axle station",
            "Distance from rearmost axle-group centre (m; positive forward):",
            0.0,
            -100.0,
            100.0,
            2,
        )
        if not accepted:
            return
        stations = self.units_table.item(row, 4).text().strip()
        self.units_table.item(row, 4).setText(
            f"{stations}, {station:.2f}" if stations else f"{station:.2f}"
        )

    def _remove_axle(self) -> None:
        row = self.units_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Select a component", "Select a component before removing an axle.")
            return
        stations = [
            value.strip()
            for value in self.units_table.item(row, 4).text().split(",")
            if value.strip()
        ]
        if not stations:
            return
        selected, accepted = QInputDialog.getItem(
            self, "Remove axle station", "Choose station to remove:", stations, 0, False
        )
        if accepted:
            stations.remove(selected)
            self.units_table.item(row, 4).setText(", ".join(stations))

    def _remove_unit(self) -> None:
        row = self.units_table.currentRow()
        if row >= 0:
            self.units_table.removeRow(row)

    def _move_unit(self, delta: int) -> None:
        row = self.units_table.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self.units_table.rowCount():
            return
        values = [
            self.units_table.item(row, column).text()
            for column in range(self.units_table.columnCount())
        ]
        self.units_table.removeRow(row)
        self.units_table.insertRow(target)
        for column, value in enumerate(values):
            self.units_table.setItem(target, column, QTableWidgetItem(value))
        self.units_table.selectRow(target)

    def _accept_geometry(self) -> None:
        try:
            units: list[dict[str, object]] = []
            for row in range(self.units_table.rowCount()):
                item = lambda column: self.units_table.item(row, column).text().strip()
                axle_offsets = tuple(
                    float(value.strip())
                    for value in item(4).split(",")
                    if value.strip()
                )
                units.append(
                    {
                        "name": item(0),
                        "width_m": float(item(1)),
                        "front_extent_m": float(item(2)),
                        "rear_extent_m": float(item(3)),
                        "axle_offsets_m": axle_offsets,
                        "track_m": float(item(5)),
                        "front_hitch_m": float(item(6)),
                        "rear_hitch_m": float(item(7)),
                        "hitch_width_m": float(item(8)),
                    }
                )
            data = {
                "name": self.name_edit.text().strip(),
                "steer_axle_m": self.steer_axle_spin.value(),
                "max_steer_deg": self.max_steer_spin.value(),
                "max_articulation_deg": self.max_articulation_spin.value(),
                "source_reference": self.source_edit.text().strip(),
                "units": units,
            }
            self.geometry = CombinationGeometry.from_dict(data)
        except (ValueError, TypeError, KeyError, AttributeError, IndexError) as error:
            QMessageBox.warning(self, "Invalid vehicle geometry", str(error))
            return
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("OpenArch Swept Paths")
        self.resize(1280, 820)
        self.setWindowIcon(QIcon(str(asset_path("app-icon.svg"))))
        self.setStyleSheet(
            "QWidget { background: #24272b; color: #d8dde3; }"
            "QMainWindow { background: #1b1d20; }"
            "QMenuBar, QMenu { background: #24272b; color: #d8dde3; }"
            "QMenuBar::item:selected, QMenu::item:selected { background: #3a4148; }"
            "QToolBar { background: #24272b; border: 0; border-bottom: 1px solid #111315; "
            "spacing: 6px; padding: 5px; }"
            "QToolButton { color: #d8dde3; padding: 5px 8px; border-radius: 4px; }"
            "QToolButton:hover { background: #3a4148; }"
            "QToolButton:checked { background: #3d7165; color: #f1f4f2; }"
            "QDockWidget { color: #d8dde3; }"
            "QDockWidget::title { background: #30363c; color: #e5e8eb; "
            "padding: 7px; font-weight: 600; }"
            "QComboBox, QDoubleSpinBox, QLineEdit, QTableWidget { background: #181a1d; "
            "color: #e1e5e9; border: 1px solid #454b52; border-radius: 3px; padding: 5px; }"
            "QPushButton { background: #383e45; color: #e1e5e9; border: 1px solid #515860; "
            "border-radius: 4px; padding: 6px 9px; }"
            "QPushButton:hover { background: #48515a; }"
            "QHeaderView::section { background: #30363c; color: #d8dde3; padding: 5px; "
            "border: 1px solid #454b52; }"
            "QStatusBar { background: #20282c; color: #bbcbcf; border-top: 1px solid #38454b; }"
            "QToolBar::separator { background: #475359; width: 1px; margin: 7px 4px; }"
            "QToolButton:disabled, QPushButton:disabled { color: #838c92; }"
            "QToolButton:focus, QPushButton:focus, QComboBox:focus, QDoubleSpinBox:focus {"
            "border: 1px solid #8fcbb7; }"
            "QTabBar::tab { padding: 8px 14px; background: #272e32; color: #bbc4ca; }"
            "QTabBar::tab:selected { color: #e9f2ee; border-top: 2px solid #89beae; }"
            "QListWidget { background: #1b2023; border: 1px solid #455158; }"
            "QProgressBar { background: #181e20; border: 1px solid #46545a; text-align: center; }"
            "QProgressBar::chunk { background: #47796c; }"
            "QScrollArea { border: 0; }"
            "QScrollBar:vertical, QScrollBar:horizontal { background: #1b1d20; width: 12px; height: 12px; }"
            "QScrollBar::handle { background: #4b535b; border-radius: 5px; min-height: 22px; }"
        )
        self.undo_stack = QUndoStack(self)
        self.undo_stack.setUndoLimit(100)
        self._history_depth = 0
        self._history_restoring = False
        self._history_current = None
        self.project_path: Path | None = None
        self._saved_state = None
        self.document = None
        self.drawing_path: Path | None = None
        self._background_tempdir: tempfile.TemporaryDirectory[str] | None = None
        self._background_file: Path | None = None
        self.vehicle_definitions = self._load_vehicle_definitions()
        first_vehicle_id = next(iter(self.vehicle_definitions))
        self.swept_paths = [
            SweptPathDocument(
                uuid.uuid4().hex,
                "Swept path 1",
                first_vehicle_id,
                "#f28583",
                [],
            )
        ]
        self.active_path_id = self.swept_paths[0].id
        self._path_switching = False
        self.route_items: list[object] = []
        self.route_line_item: QGraphicsPathItem | None = None
        self.other_path_items: list[object] = []
        self.hover_items: list[object] = []
        self.trace_items: list[object] = []
        self.position_items: list[object] = []
        self.envelope_items: list[object] = []
        self.envelope_geometry = None
        self.simulation_result: SimulationResult | None = None
        self.simulation_scale = 1.0
        self._hover_point: tuple[float, float] | None = None
        self._preview_validity = "pending"
        self._preview_generation = 0
        self._preview_running = False
        self._preview_show_errors = False
        self._preview_fast = True
        self.preview_pool = QThreadPool(self)
        self.preview_pool.setMaxThreadCount(1)
        self.clearance_pool = QThreadPool(self)
        self.clearance_pool.setMaxThreadCount(1)
        self._clearance_pending = False
        self._clearance_running = False
        self._clearance_worker = None
        self.update_pool = QThreadPool(self)
        self.update_pool.setMaxThreadCount(1)
        self._update_check_running = False
        self._update_check_manual = False
        self.vehicle_geometries = self._load_vehicle_geometries()
        self._last_vehicle_geometry_mode: bool | None = None

        self.view = DrawingView()
        self.start_screen = StartScreen(asset_path(""))
        self.pages = QStackedWidget()
        self.pages.addWidget(self.start_screen)
        self.pages.addWidget(self.view)
        self.setCentralWidget(self.pages)
        self.start_screen.openRequested.connect(self.open_dxf)
        self.start_screen.returnRequested.connect(self._show_editor)
        self._editor_dock_state = (True, True)
        self.view.worldClicked.connect(self.add_route_point)
        self.view.positionClicked.connect(self.add_vehicle_station)
        self.view.worldMoved.connect(self.update_hover_point)
        self.view.finishRequested.connect(self.finish_route)
        self.view.insertWaypointRequested.connect(self.insert_route_point)
        self.view.deleteWaypointsRequested.connect(self.delete_selected_waypoints)

        self._build_vehicle_panel()
        self._build_display_panel()
        self._build_actions()
        self.vehicle_combo.currentIndexChanged.connect(self._vehicle_selected)
        self.length_spin.valueChanged.connect(self._vehicle_dimensions_changed)
        self.width_spin.valueChanged.connect(self._vehicle_dimensions_changed)
        self.units_spin.valueChanged.connect(self._drawing_scale_changed)
        self.corner_radius_spin.valueChanged.connect(self._vehicle_dimensions_changed)
        self.speed_spin.valueChanged.connect(self._vehicle_dimensions_changed)
        self.steering_limit_spin.valueChanged.connect(self._vehicle_dimensions_changed)
        self.lock_to_lock_spin.valueChanged.connect(self._vehicle_dimensions_changed)
        self.dry_steering_check.toggled.connect(self._vehicle_dimensions_changed)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(100)
        self.preview_timer.timeout.connect(self._preview_current_route)
        self.playback_timer = QTimer(self)
        self.playback_timer.setInterval(50)
        self.playback_timer.timeout.connect(self._advance_playback)

        self.status = QStatusBar()
        self.status.showMessage("Open a DXF or DWG site drawing to begin")
        self.setStatusBar(self.status)
        self._vehicle_selected()
        self._show_start_screen()
        QTimer.singleShot(1800, self._automatic_update_check)

    def _show_start_screen(self):
        if self.pages.currentWidget() == self.view:
            self._editor_dock_state = tuple(
                not dock.isHidden() for dock in (self.vehicle_dock, self.display_dock)
            )
            if self.view.drawing_mode or self.view.position_mode:
                self.finish_route()
        self.pages.setCurrentWidget(self.start_screen)
        self.start_screen.return_button.setVisible(self.document is not None)
        for dock in (self.vehicle_dock, self.display_dock):
            dock.hide()
            dock.toggleViewAction().setEnabled(False)
        for toolbar in self.editor_toolbars:
            toolbar.hide()
        self.edit_menu.setEnabled(False)
        self.status.hide()
        self.start_screen.open_button.setFocus()

    def _show_editor(self):
        if self.document is None:
            return
        self.pages.setCurrentWidget(self.view)
        for dock, visible in zip((self.vehicle_dock, self.display_dock), self._editor_dock_state):
            dock.setVisible(visible)
            dock.toggleViewAction().setEnabled(True)
        for toolbar in self.editor_toolbars:
            toolbar.show()
        self.edit_menu.setEnabled(True)
        self.status.show()
        self.view.setFocus()

    @property
    def active_path(self) -> SweptPathDocument:
        return next(path for path in self.swept_paths if path.id == self.active_path_id)

    @staticmethod
    def _version_key(value: str):
        match = re.fullmatch(
            r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?", value.strip()
        )
        if not match:
            return None
        major, minor, patch = (int(match.group(i)) for i in (1, 2, 3))
        prerelease = match.group(4) or ""
        return major, minor, patch, 0 if prerelease else 1, prerelease

    def _automatic_update_check(self) -> None:
        if QSettings("OpenArch", "OpenArchSweptPaths").value("checkForUpdatesAutomatically", True, type=bool):
            self.check_for_updates(manual=False)

    def check_for_updates(self, *, manual: bool = True) -> None:
        if self._update_check_running:
            self._update_check_manual |= manual
            return
        self._update_check_running = True
        self._update_check_manual = manual
        self.update_action.setEnabled(False)
        worker = _UpdateCheckWorker()
        worker.signals.finished.connect(self._update_check_finished)
        self._update_worker = worker
        self.update_pool.start(worker)

    def _update_check_finished(self, release, error: str) -> None:
        self._update_check_running = False
        self.update_action.setEnabled(True)
        manual = self._update_check_manual
        self._update_check_manual = False
        self._update_worker = None
        if error or not isinstance(release, dict):
            if manual:
                QMessageBox.warning(self, "Update check failed", error or "The release response was invalid.")
            return
        tag = str(release.get("tag_name", ""))
        latest, current = self._version_key(tag), self._version_key(__version__)
        if latest is None or current is None:
            if manual:
                QMessageBox.warning(self, "Update check failed", "Could not compare the release version.")
            return
        if latest <= current:
            if manual:
                QMessageBox.information(self, "No updates available", f"OpenArch Swept Paths {__version__} is current.")
            return
        settings = QSettings("OpenArch", "OpenArchSweptPaths")
        if not manual and settings.value("lastNotifiedRelease", "") == tag:
            return
        settings.setValue("lastNotifiedRelease", tag)
        url = str(release.get("html_url", ""))
        if not url.startswith("https://github.com/kristiangl/OpenArchSP/releases/"):
            url = "https://github.com/kristiangl/OpenArchSP/releases/latest"
        message = QMessageBox(self)
        message.setWindowTitle("OpenArch Swept Paths update available")
        message.setText(f"Version {tag.removeprefix('v')} is available. You are using {__version__}.")
        message.setInformativeText("Open the release page to download the installer for this computer.")
        message.setDetailedText(str(release.get("body") or "No release notes were provided.")[:5000])
        open_button = message.addButton("Open release page", QMessageBox.ButtonRole.AcceptRole)
        message.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        message.exec()
        if message.clickedButton() == open_button:
            QDesktopServices.openUrl(QUrl(url))

    def _automatic_updates_toggled(self, enabled: bool) -> None:
        QSettings("OpenArch", "OpenArchSweptPaths").setValue("checkForUpdatesAutomatically", enabled)

    @property
    def route_points(self) -> list[tuple[float, float]]:
        return self.active_path.points

    def _refresh_path_picker(self) -> None:
        self.path_combo.blockSignals(True)
        self.path_combo.clear()
        for path in self.swept_paths:
            self.path_combo.addItem(path.name, path.id)
        index = self.path_combo.findData(self.active_path_id)
        if index >= 0:
            self.path_combo.setCurrentIndex(index)
        self.path_combo.blockSignals(False)

    @undoable("New path")
    def new_swept_path(self) -> None:
        number = len(self.swept_paths) + 1
        colors = ("#f28583", "#70b7d3", "#d0a45b", "#a590cf", "#7fbd91", "#da7da8")
        path = SweptPathDocument(
            uuid.uuid4().hex,
            f"Swept path {number}",
            str(self.vehicle_combo.currentData()),
            colors[(number - 1) % len(colors)],
            [],
        )
        path.settings = self._settings()
        self.swept_paths.append(path)
        self._refresh_path_picker()
        self.path_combo.setCurrentIndex(self.path_combo.findData(path.id))

    @undoable("Rename path")
    def rename_swept_path(self) -> None:
        path = self.active_path
        name, accepted = QInputDialog.getText(
            self, "Rename swept path", "Path name:", text=path.name
        )
        if not accepted or not name.strip():
            return
        path.name = name.strip()
        self._refresh_path_picker()
        self._render_other_paths()

    @undoable("Remove path")
    def remove_swept_path(self) -> None:
        if len(self.swept_paths) == 1:
            self.clear_route()
            return
        self.swept_paths = [path for path in self.swept_paths if path.id != self.active_path_id]
        self._refresh_path_picker()
        self._path_selection_changed(0)

    def _path_selection_changed(self, index: int) -> None:
        if index < 0:
            return
        path_id = str(self.path_combo.itemData(index))
        if not path_id or path_id == self.active_path_id:
            return
        for previous in self.swept_paths:
            if previous.id == self.active_path_id:
                previous.settings = self._settings()
                if not previous.detailed:
                    previous.simulation_result = None
                    previous.envelope_geometry = None
        self.preview_timer.stop()
        self._preview_generation += 1
        self._hover_point = None
        self._stop_vehicle_station_mode()
        self.view.drawing_mode = False
        self.finish_action.setEnabled(False)
        self.draw_action.setChecked(False)
        self._render_hover_cursor()
        for item in self.route_items:
            self.view.scene.removeItem(item)
        self.route_items.clear()
        self.route_line_item = None
        self._clear_envelope(clear_record=False)
        self.active_path_id = path_id
        self._path_switching = True
        vehicle_index = self.vehicle_combo.findData(self.active_path.vehicle_id)
        if vehicle_index >= 0:
            self.vehicle_combo.setCurrentIndex(vehicle_index)
        self._vehicle_selected()
        self._apply_settings(self.active_path.settings)
        self._sync_direction_action()
        self._path_switching = False
        self._refresh_vehicle_station_list()
        self._render_route()
        self._render_other_paths()
        if self.active_path.simulation_result and self.active_path.envelope_geometry is not None:
            self.simulation_result = self.active_path.simulation_result
            self.playback_slider.setEnabled(True)
            self.playback_slider.setValue(0)
            self.envelope_geometry = self.active_path.envelope_geometry
            self.simulation_scale = self.active_path.simulation_scale
            self._render_envelope(self.envelope_geometry)
            self._render_simulation_traces(
                self.simulation_result,
                self.vehicle_geometries[self.active_path.vehicle_id],
                self.simulation_scale,
            )
            self._render_current_vehicle(
                self.simulation_result,
                self.vehicle_geometries[self.active_path.vehicle_id],
                self.simulation_scale,
            )
        elif self.active_path.envelope_geometry is not None:
            self.playback_slider.setEnabled(False)
            self.envelope_geometry = self.active_path.envelope_geometry
            self._render_envelope(self.envelope_geometry)
        elif len(self.route_points) >= 2 and self.document is not None:
            self.calculate_envelope(show_errors=False)
        self._update_export_state()
        if not self._history_depth and not self._history_restoring:
            self._history_current = self._capture_history()

    def _load_vehicle_definitions(self) -> dict[str, VehicleDefinition]:
        definitions = {vehicle.id: vehicle for vehicle in STANDARD_VEHICLES}
        definitions[CUSTOM_RIGID_VEHICLE.id] = CUSTOM_RIGID_VEHICLE
        settings = QSettings("OpenArch", "OpenArchSweptPaths")
        try:
            saved = json.loads(settings.value("customVehicles", "[]"))
            for entry in saved:
                vehicle = VehicleDefinition.from_dict(entry)
                if vehicle.id not in definitions:
                    definitions[vehicle.id] = vehicle
        except (TypeError, ValueError, KeyError):
            pass
        return definitions

    def _load_vehicle_geometries(self) -> dict[str, CombinationGeometry]:
        settings = QSettings("OpenArch", "OpenArchSweptPaths")
        profiles = standard_vehicle_geometries()
        try:
            saved = json.loads(settings.value("vehicleGeometries", "{}"))
            for vehicle_id, entry in saved.items():
                profiles[str(vehicle_id)] = CombinationGeometry.from_dict(entry)
        except (TypeError, ValueError, KeyError, AttributeError):
            pass
        return profiles

    def _build_vehicle_panel(self) -> None:
        dock = QDockWidget("Vehicle and units", self)
        panel = QWidget()
        form = QFormLayout(panel)
        form.setContentsMargins(14, 16, 14, 16)
        form.setVerticalSpacing(9)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        def section_title(text):
            label = QLabel(text)
            label.setStyleSheet("color: #9ac4b6; font-weight: 600; padding-top: 5px;")
            form.addRow(label)

        section_title("Vehicle profile")
        self.vehicle_combo = QComboBox()
        self.vehicle_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.vehicle_combo.setMinimumContentsLength(16)
        for vehicle in self.vehicle_definitions.values():
            self.vehicle_combo.addItem(vehicle.name, vehicle.id)
        form.addRow("Design vehicle", self.vehicle_combo)

        self.length_spin = self._dimension_spin(100.0, 5.2)
        self.width_spin = self._dimension_spin(20.0, 1.9)
        self.units_spin = QDoubleSpinBox()
        self.units_spin.setKeyboardTracking(False)
        self.units_spin.setRange(0.000001, 1_000_000_000)
        self.units_spin.setDecimals(6)
        self.units_spin.setSingleStep(1)
        self.units_spin.setValue(1)
        self.units_spin.setToolTip(
            "Drawing units corresponding to one metre. DXF $INSUNITS is used when available."
        )

        form.addRow("Length (m)", self.length_spin)
        form.addRow("Width (m)", self.width_spin)
        section_title("Path settings")
        self.corner_radius_spin = self._dimension_spin(100.0, 12.5)
        self.corner_radius_spin.setToolTip(
            "Radius used to round path waypoints into tangential turns."
        )
        form.addRow("Waypoint turn radius (m)", self.corner_radius_spin)
        self.minimum_radius_button = QPushButton("Use minimum steer-axle radius")
        self.minimum_radius_button.setToolTip(
            "Uses the lead vehicle's bicycle-model wheelbase and steering limit. "
            "Articulated trailer limits still need checking."
        )
        self.minimum_radius_button.clicked.connect(self._use_minimum_turn_radius)
        form.addRow(self.minimum_radius_button)
        self.speed_spin = self._dimension_spin(100.0, 5.0)
        form.addRow("Travel speed (km/h)", self.speed_spin)
        self.steering_limit_spin = self._dimension_spin(60.0, 35.0)
        self.steering_limit_spin.setToolTip(
            "Maximum centre road-wheel angle used by the live steering simulation. "
            "This overrides the vehicle profile for the current run."
        )
        form.addRow("Road-wheel limit (°)", self.steering_limit_spin)
        self.lock_to_lock_spin = self._dimension_spin(30.0, 6.0)
        self.lock_to_lock_spin.setToolTip(
            "Time for the road wheels to move from full lock one way to full lock the other."
        )
        form.addRow("Steering lock-to-lock (s)", self.lock_to_lock_spin)
        self.dry_steering_check = QCheckBox("Allow dry steering")
        self.dry_steering_check.setToolTip(
            "Allows steering adjustment while stopped and removes lock-to-lock timing "
            "from the spatial solution. Stop duration and position are not modelled."
        )
        form.addRow(self.dry_steering_check)

        form.addRow("Drawing units per metre", self.units_spin)
        section_title("Profile details")
        self.vehicle_source_label = QLabel()
        self.vehicle_source_label.setWordWrap(True)
        form.addRow("Source / model", self.vehicle_source_label)

        self.vehicle_note_label = QLabel()
        self.vehicle_note_label.setWordWrap(True)
        self.vehicle_note_label.setTextFormat(Qt.TextFormat.PlainText)
        self.vehicle_note_label.setStyleSheet("color: #d0c4aa;")
        notes_button = QToolButton()
        notes_button.setCheckable(True)
        notes_button.setText("Dimensions and assumptions")
        notes_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        notes_button.setArrowType(Qt.ArrowType.RightArrow)
        notes_button.toggled.connect(self.vehicle_note_label.setVisible)
        notes_button.toggled.connect(lambda opened: notes_button.setArrowType(
            Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow))
        form.addRow(notes_button)
        form.addRow(self.vehicle_note_label)
        self.vehicle_note_label.hide()

        self.save_vehicle_button = command_button("Save custom vehicle", "project")
        self.save_vehicle_button.clicked.connect(self.save_custom_vehicle)
        vehicle_buttons = QHBoxLayout()
        vehicle_buttons.addWidget(self.save_vehicle_button)

        self.edit_geometry_button = command_button("Edit geometry", "geometry")
        self.edit_geometry_button.clicked.connect(self.edit_vehicle_geometry)
        vehicle_buttons.addWidget(self.edit_geometry_button)
        form.addRow(vehicle_buttons)

        self.calculate_action = QAction("Detailed swept path", self)
        self.calculate_action.triggered.connect(lambda: self.calculate_envelope())
        self.calculate_action.setEnabled(False)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(320)
        scroll.setWidget(panel)
        dock.setWidget(scroll)
        self.vehicle_dock = dock
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

    def _build_display_panel(self) -> None:
        settings = QSettings("OpenArch", "OpenArchSweptPaths")
        dock = QDockWidget("Display", self)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.outside_visible = QCheckBox("OASP-Outside · swept envelope", panel)
        self.vehicle_visible = QCheckBox("OASP-Vehicle · body and axle lines", panel)
        self.wheels_visible = QCheckBox("OASP-Wheel · wheel tracks and blocks", panel)
        for key, checkbox in (
            ("showOutside", self.outside_visible),
            ("showVehicle", self.vehicle_visible),
            ("showWheels", self.wheels_visible),
        ):
            checkbox.setChecked(settings.value(key, True, type=bool))
            layout.addWidget(checkbox)

        layout.addSpacing(8)
        self.show_start_position = QCheckBox("Show start position", panel)
        self.show_end_position = QCheckBox("Show end position", panel)
        self.show_start_position.setChecked(settings.value("showStartPosition", True, type=bool))
        self.show_end_position.setChecked(settings.value("showEndPosition", True, type=bool))
        layout.addWidget(self.show_start_position)
        layout.addWidget(self.show_end_position)
        layout.addWidget(QLabel("Additional vehicle positions", panel))
        self.vehicle_station_list = QListWidget(panel)
        self.vehicle_station_list.setMaximumHeight(160)
        layout.addWidget(self.vehicle_station_list)
        station_buttons = QHBoxLayout()
        self.add_station_button = command_button("Place on route", "add", panel)
        self.remove_station_button = command_button("Remove", "remove", panel)
        self.add_station_button.clicked.connect(self._begin_vehicle_station_mode)
        self.remove_station_button.clicked.connect(self.remove_vehicle_station)
        station_buttons.addWidget(self.add_station_button)
        station_buttons.addWidget(self.remove_station_button)
        layout.addLayout(station_buttons)
        playback_row = QHBoxLayout()
        self.playback_button = QPushButton("Play route", panel)
        self.playback_button.clicked.connect(self._toggle_playback)
        self.playback_slider = QSlider(Qt.Orientation.Horizontal, panel)
        self.playback_slider.setRange(0, 1000)
        self.playback_slider.setEnabled(False)
        self.playback_slider.valueChanged.connect(self._playback_slider_changed)
        playback_row.addWidget(self.playback_button)
        playback_row.addWidget(self.playback_slider, 1)
        layout.addLayout(playback_row)
        layout.addWidget(QLabel("Site layers for clearance check", panel))
        self.clearance_layer_list = QListWidget(panel)
        self.clearance_layer_list.setMaximumHeight(120)
        layout.addWidget(self.clearance_layer_list)
        clearance_buttons = QHBoxLayout()
        self.select_clearance_layers_button = QPushButton("Select all", panel)
        self.select_clearance_layers_button.clicked.connect(self._toggle_clearance_layers)
        self.check_clearance_button = QPushButton("Check clearance", panel)
        self.check_clearance_button.clicked.connect(self.check_clearance)
        clearance_buttons.addWidget(self.select_clearance_layers_button)
        clearance_buttons.addWidget(self.check_clearance_button)
        layout.addLayout(clearance_buttons)
        self.clearance_status_label = QLabel("No clearance check has been run", panel)
        self.clearance_status_label.setWordWrap(True)
        layout.addWidget(self.clearance_status_label)
        layout.addStretch(1)
        dock.setWidget(panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

        self.tabifyDockWidget(self.vehicle_dock, dock)
        self.vehicle_dock.raise_()
        self.display_dock = dock
        self.resizeDocks([self.vehicle_dock], [360], Qt.Orientation.Horizontal)
        self.outside_visible.toggled.connect(
            lambda visible: self._set_display_option("showOutside", visible, "outside")
        )
        self.vehicle_visible.toggled.connect(
            lambda visible: self._set_display_option("showVehicle", visible, "vehicle")
        )
        self.wheels_visible.toggled.connect(
            lambda visible: self._set_display_option("showWheels", visible, "wheel")
        )
        self.show_start_position.toggled.connect(
            lambda visible: self._set_position_option("showStartPosition", visible)
        )
        self.show_end_position.toggled.connect(
            lambda visible: self._set_position_option("showEndPosition", visible)
        )

    @undoable("Change display")
    def _set_display_option(self, key: str, visible: bool, group: str) -> None:
        QSettings("OpenArch", "OpenArchSweptPaths").setValue(key, visible)
        for item in self.view.scene.items():
            if item.data(0) == group:
                item.setVisible(visible)

    @undoable("Change vehicle display")
    def _set_position_option(self, key: str, visible: bool) -> None:
        QSettings("OpenArch", "OpenArchSweptPaths").setValue(key, visible)
        if self.simulation_result is not None:
            self._render_travel_positions(
                self.simulation_result,
                self.vehicle_geometries[self.active_path.vehicle_id],
                self.simulation_scale,
            )

    def _toggle_playback(self) -> None:
        if self.simulation_result is None:
            self.status.showMessage("Calculate a vehicle path before playback")
        elif self.playback_timer.isActive():
            self.playback_timer.stop()
            self.playback_button.setText("Play route")
        else:
            if self.playback_slider.value() >= self.playback_slider.maximum():
                self.playback_slider.setValue(0)
            self.playback_button.setText("Pause")
            self.playback_timer.start()

    def _advance_playback(self) -> None:
        value = self.playback_slider.value() + 5
        if value >= self.playback_slider.maximum():
            value = self.playback_slider.maximum()
            self.playback_timer.stop()
            self.playback_button.setText("Play route")
        self.playback_slider.setValue(value)

    def _playback_slider_changed(self, value: int) -> None:
        if self.simulation_result is not None:
            self._render_travel_positions(
                self.simulation_result,
                self.vehicle_geometries[self.active_path.vehicle_id],
                self.simulation_scale,
                playback_fraction=value / self.playback_slider.maximum(),
            )

    def _begin_vehicle_station_mode(self) -> None:
        if self.simulation_result is None or len(self.simulation_result.steer_axle_path) < 2:
            self.status.showMessage("Calculate a swept path before placing vehicle positions")
            return
        self.view.position_mode = True
        self.view.drawing_mode = False
        self.view.setFocus()
        self.view.setCursor(Qt.CursorShape.CrossCursor)
        self.status.showMessage("Click on the tracked route to place a vehicle position · Esc to stop")

    def _stop_vehicle_station_mode(self) -> None:
        self.view.position_mode = False
        self.view.setCursor(Qt.CursorShape.ArrowCursor)

    @undoable("Place vehicle")
    def add_vehicle_station(self, scene_x: float, scene_y: float) -> None:
        self._stop_vehicle_station_mode()
        route = self.simulation_result.steer_axle_path if self.simulation_result else ()
        if len(route) < 2:
            return
        scene_x /= self.simulation_scale
        scene_y /= self.simulation_scale
        cumulative = [0.0]
        for first, second in zip(route, route[1:]):
            cumulative.append(cumulative[-1] + math.dist(first, second))
        best_distance = math.inf
        station_distance = 0.0
        for index, (first, second) in enumerate(zip(route, route[1:])):
            dx, dy = second[0] - first[0], second[1] - first[1]
            length_squared = dx * dx + dy * dy
            fraction = min(1.0, max(0.0, ((scene_x-first[0])*dx + (scene_y-first[1])*dy) / max(length_squared, 1e-12)))
            px, py = first[0] + dx*fraction, first[1] + dy*fraction
            distance_squared = (px-scene_x)**2 + (py-scene_y)**2
            if distance_squared < best_distance:
                best_distance = distance_squared
                station_distance = cumulative[index] + math.sqrt(length_squared) * fraction
        total = cumulative[-1]
        if total <= 0:
            return
        fraction = station_distance / total
        if any(abs(existing - fraction) < 0.005 for existing in self.active_path.vehicle_stations):
            self.status.showMessage("A vehicle position is already set at this route location")
            return
        self.active_path.vehicle_stations.append(fraction)
        self.active_path.vehicle_stations.sort()
        self._refresh_vehicle_station_list()
        self._render_travel_positions(
            self.simulation_result,
            self.vehicle_geometries[self.active_path.vehicle_id],
            self.simulation_scale,
        )

    @undoable("Remove vehicle position")
    def remove_vehicle_station(self) -> None:
        row = self.vehicle_station_list.currentRow()
        if row < 0 or row >= len(self.active_path.vehicle_stations):
            return
        self.active_path.vehicle_stations.pop(row)
        self._refresh_vehicle_station_list()
        if self.simulation_result is not None:
            self._render_travel_positions(
                self.simulation_result,
                self.vehicle_geometries[self.active_path.vehicle_id],
                self.simulation_scale,
            )

    def _refresh_vehicle_station_list(self) -> None:
        self.vehicle_station_list.clear()
        for fraction in self.active_path.vehicle_stations:
            self.vehicle_station_list.addItem(f"{fraction * 100:.1f}% along route")

    def _populate_clearance_layers(self) -> None:
        self.clearance_layer_list.clear()
        if self.document is not None:
            for layer in modelspace_layers(self.document):
                item = QListWidgetItem(layer, self.clearance_layer_list)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)

    def _toggle_clearance_layers(self) -> None:
        items = [self.clearance_layer_list.item(i) for i in range(self.clearance_layer_list.count())]
        checked = bool(items) and all(item.checkState() == Qt.CheckState.Checked for item in items)
        state = Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked
        for item in items:
            item.setCheckState(state)
        self.select_clearance_layers_button.setText("Select all" if checked else "Clear selection")

    def check_clearance(self) -> None:
        selected = any(
            self.clearance_layer_list.item(i).checkState() == Qt.CheckState.Checked
            for i in range(self.clearance_layer_list.count())
        )
        if self.document is None or len(self.route_points) < 2:
            self.clearance_status_label.setText("Open a drawing and define a path first.")
        elif not selected:
            self.clearance_status_label.setText("Select one or more site layers to check.")
        elif self.envelope_geometry is None:
            self._clearance_pending = True
            self.clearance_status_label.setText("Calculating swept envelope before clearance check…")
            self.calculate_envelope(show_errors=False)
        else:
            self._run_clearance_check()

    def _run_clearance_check(self) -> None:
        if self._clearance_running or self.document is None or self.envelope_geometry is None:
            return
        selected = [
            self.clearance_layer_list.item(i).text()
            for i in range(self.clearance_layer_list.count())
            if self.clearance_layer_list.item(i).checkState() == Qt.CheckState.Checked
        ]
        self._clearance_pending = False
        if not selected:
            return
        self._clearance_running = True
        self.check_clearance_button.setEnabled(False)
        self.clearance_status_label.setText("Checking selected site layers…")
        worker = _ClearanceWorker(self.envelope_geometry, self.document, selected, self.units_spin.value())
        worker.signals.finished.connect(self._clearance_completed)
        self._clearance_worker = worker
        self.clearance_pool.start(worker)

    def _clearance_completed(self, summary, error: str) -> None:
        self._clearance_running = False
        self._clearance_worker = None
        self.check_clearance_button.setEnabled(True)
        if error or not isinstance(summary, dict):
            self.clearance_status_label.setText(f"Clearance check failed: {error or 'invalid result'}")
            return
        collisions, minimum = summary["collision_layers"], summary["minimum_clearance_m"]
        if collisions:
            text = "Envelope intersects: " + ", ".join(collisions)
        elif minimum is None:
            text = "No supported geometry was found on selected layers."
        else:
            closest = min(summary["per_layer"].items(), key=lambda pair: pair[1]["clearance_m"])[0]
            text = f"Minimum clearance: {minimum:.2f} m to {closest}"
        self.active_path.clearance_summary = summary
        self.clearance_status_label.setText(text)

    @staticmethod
    def _dimension_spin(maximum: float, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setKeyboardTracking(False)
        spin.setRange(0.1, maximum)
        spin.setDecimals(3)
        spin.setSingleStep(0.1)
        spin.setValue(value)
        return spin

    def _build_actions(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self.open_action = QAction("&Open drawing or project…", self)
        self.open_action.setIcon(action_icon("open"))
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.open_dxf)
        file_menu.addAction(self.open_action)

        self.project_action = QAction(action_icon("project"), "Save project", self)
        self.project_action.setShortcut(QKeySequence.StandardKey.Save)
        self.project_action.triggered.connect(self.save_editable_project)
        file_menu.addAction(self.project_action)
        self.project_as_action = file_menu.addAction("Save project as…")
        self.project_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        self.project_as_action.triggered.connect(lambda: self.save_editable_project(save_as=True))
        file_menu.addSeparator()

        self.export_action = QAction("Export swept paths as DXF…", self)
        self.export_action.setIcon(action_icon("export"))
        self.export_action.triggered.connect(self.export_path)
        self.export_action.setEnabled(False)
        file_menu.addAction(self.export_action)

        self.save_copy_action = QAction("Save drawing copy with swept paths as…", self)
        self.save_copy_action.setIcon(action_icon("save-copy"))
        self.save_copy_action.triggered.connect(self.save_drawing_copy)
        self.save_copy_action.setEnabled(False)
        file_menu.addAction(self.save_copy_action)
        self.report_action = QAction("Export path analysis report…", self)
        self.report_action.triggered.connect(self.export_analysis_report)
        file_menu.addAction(self.report_action)

        view_menu = self.menuBar().addMenu("&View")
        fit_action = QAction("Zoom to &Extents", self)
        fit_action.setIcon(action_icon("zoom-extents"))
        fit_action.setShortcut("Z")
        fit_action.triggered.connect(self.view.fit_drawing)
        view_menu.addAction(fit_action)
        view_menu.addSeparator()
        view_menu.addAction(self.vehicle_dock.toggleViewAction())
        view_menu.addAction(self.display_dock.toggleViewAction())

        vehicle_menu = self.menuBar().addMenu("&Vehicle")
        self.reverse_action = QAction("Direction: Forward", self)
        self.reverse_action.setCheckable(True)
        self.reverse_action.setToolTip("Toggle forward and reverse travel for this path")
        self.reverse_action.toggled.connect(self._direction_toggled)
        vehicle_menu.addAction(self.reverse_action)

        toolbar = QToolBar("Path")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(22, 22))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.addToolBar(toolbar)
        self.editor_toolbars = [toolbar]
        self.open_action.setIconText("Open")
        toolbar.addAction(self.open_action)
        toolbar.addAction(self.project_action)
        toolbar.addSeparator()

        self.path_combo = QComboBox()
        self.path_combo.setMinimumContentsLength(12)
        self.path_combo.setAccessibleName("Active swept path")
        self.path_combo.setToolTip("Active swept path")
        self._refresh_path_picker()
        self.path_combo.currentIndexChanged.connect(self._path_selection_changed)
        toolbar.addWidget(self.path_combo)
        new_path_action = QAction(action_icon("add"), "New path", self)
        new_path_action.triggered.connect(self.new_swept_path)
        toolbar.addAction(new_path_action)
        rename_path_action = QAction(action_icon("rename"), "Rename", self)
        rename_path_action.triggered.connect(self.rename_swept_path)
        toolbar.addAction(rename_path_action)
        remove_path_action = QAction(action_icon("remove"), "Remove", self)
        remove_path_action.triggered.connect(self.remove_swept_path)
        toolbar.addAction(remove_path_action)
        toolbar.addSeparator()
        toolbar.addAction(self.reverse_action)
        toolbar.addSeparator()

        self.draw_action = QAction("Draw reference path", self)
        self.draw_action.setIcon(action_icon("draw-path"))
        self.draw_action.setCheckable(True)
        self.draw_action.setEnabled(False)
        self.draw_action.triggered.connect(self.set_drawing_mode)
        toolbar.addAction(self.draw_action)

        self.finish_action = QAction("Finish path", self)
        self.finish_action.setIcon(action_icon("finish-path"))
        self.finish_action.setShortcut(QKeySequence("Return"))
        self.finish_action.setEnabled(False)
        self.finish_action.triggered.connect(self.finish_route)
        toolbar.addAction(self.finish_action)

        edit_menu = QMenu("&Edit", self)
        self.edit_menu = edit_menu
        self.menuBar().insertMenu(view_menu.menuAction(), edit_menu)
        self.undo_action = self.undo_stack.createUndoAction(self, "Undo")
        self.undo_action.setIcon(action_icon("undo"))
        self.undo_action.setShortcuts(QKeySequence.keyBindings(QKeySequence.StandardKey.Undo))
        self.redo_action = self.undo_stack.createRedoAction(self, "Redo")
        self.redo_action.setIcon(action_icon("redo"))
        self.redo_action.setShortcuts(QKeySequence.keyBindings(QKeySequence.StandardKey.Redo))
        for action in (self.undo_action, self.redo_action):
            edit_menu.addAction(action)
            toolbar.addAction(action)
        self.undo_action.setIconText("Undo")
        self.redo_action.setIconText("Redo")
        remove_point = edit_menu.addAction("Remove last waypoint")
        remove_point.setShortcut(QKeySequence("Backspace"))
        remove_point.triggered.connect(self.undo_route_point)

        clear_action = QAction("Clear path", self)
        clear_action.setIcon(action_icon("clear-path"))
        clear_action.triggered.connect(self.clear_route)
        toolbar.addAction(clear_action)
        insert_action = QAction("Insert waypoint", self)
        insert_action.triggered.connect(self.arm_waypoint_insert)
        toolbar.addAction(insert_action)
        delete_waypoints_action = QAction("Delete selected waypoints", self)
        delete_waypoints_action.setShortcut(QKeySequence.StandardKey.Delete)
        delete_waypoints_action.triggered.connect(self.delete_selected_waypoints)
        toolbar.addAction(delete_waypoints_action)

        self.calculate_action.setIconText("Calculate")
        self.export_action.setIconText("Paths DXF")
        self.save_copy_action.setIconText("Drawing DXF")
        toolbar.addAction(self.calculate_action)
        toolbar.addSeparator()
        toolbar.addAction(self.export_action)
        toolbar.addAction(self.save_copy_action)
        toolbar.addSeparator()
        fit_action.setIconText("Fit view")
        toolbar.addAction(fit_action)
        self.calculate_action.setIcon(action_icon("calculate"))
        document_toolbar = QToolBar("File and view")
        document_toolbar.setMovable(False)
        document_toolbar.setIconSize(QSize(22, 22))
        document_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.insertToolBar(toolbar, document_toolbar)
        self.insertToolBarBreak(toolbar)
        self.editor_toolbars.insert(0, document_toolbar)
        for action in (self.open_action, self.project_action, self.export_action, self.save_copy_action, fit_action):
            toolbar.removeAction(action)
            document_toolbar.addAction(action)
        # Remove separators left at the ends after moving file commands to their own row.
        for action in list(toolbar.actions()):
            if action.isSeparator() and (
                action == toolbar.actions()[0] or action == toolbar.actions()[-1]
            ):
                toolbar.removeAction(action)

        help_menu = self.menuBar().addMenu("&Help")
        self.update_action = help_menu.addAction("Check for Updates…")
        self.update_action.triggered.connect(lambda: self.check_for_updates(manual=True))
        self.auto_update_action = QAction("Automatically check for updates", self)
        self.auto_update_action.setCheckable(True)
        self.auto_update_action.setChecked(
            QSettings("OpenArch", "OpenArchSweptPaths").value("checkForUpdatesAutomatically", True, type=bool)
        )
        self.auto_update_action.toggled.connect(self._automatic_updates_toggled)
        help_menu.addAction(self.auto_update_action)
        start_action = help_menu.addAction("Getting started")
        start_action.triggered.connect(self._show_start_screen)

    def open_dxf(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Open drawing or project",
            "",
            "Drawings and projects (*.dwg *.dxf *.oasp);;OpenArch projects (*.oasp);;DXF drawings (*.dxf);;DWG drawings (*.dwg);;All files (*)",
        )
        if filename:
            self.load_drawing(filename)

    def load_drawing(self, filename: str) -> None:
        path = Path(filename)
        if not self._confirm_replace():
            return
        new_tempdir = tempfile.TemporaryDirectory(prefix="openarchsp-")
        dialog = LoadingDialog(self, path)
        worker = _LoadWorker(path, Path(new_tempdir.name))
        worker.signals.progress.connect(dialog.update_stage)
        worker.signals.finished.connect(dialog.finish)
        QThreadPool.globalInstance().start(worker)
        dialog.exec()
        payload, error = dialog.payload, dialog.error
        dialog.deleteLater()
        if payload is None:
            new_tempdir.cleanup()
            QMessageBox.critical(self, "Could not open file", error)
            return
        document, project, shapes, count, warnings = payload
        scene, drawing_bounds = make_scene(shapes, self.view)
        self.preview_timer.stop()
        self._preview_generation += 1
        previous_scene = self.view.scene
        self.view.setScene(scene)
        self.view.scene = scene
        previous_scene.deleteLater()
        if self._background_tempdir:
            self._background_tempdir.cleanup()
        self._background_tempdir = new_tempdir
        self._background_file = Path(new_tempdir.name) / "background.dxf"
        self.document = document
        self.drawing_path = path
        self._populate_clearance_layers()
        self.project_path = path if project else None
        self.view.drawing_bounds = drawing_bounds
        margin = max(drawing_bounds.width(), drawing_bounds.height(), 1000.0) * 20
        self.view.scene.setSceneRect(
            drawing_bounds.adjusted(-margin, -margin, margin, margin)
        )
        # All overlay references below belong to the previous scene.
        self.route_items.clear()
        self.route_line_item = None
        self.other_path_items.clear()
        self.hover_items.clear()
        self.trace_items.clear()
        self.position_items.clear()
        self.envelope_items.clear()
        self.envelope_geometry = None
        self.simulation_result = None
        self._path_switching = True
        selected = self.vehicle_combo.currentData()
        self.vehicle_definitions = self._load_vehicle_definitions()
        self.vehicle_geometries = self._load_vehicle_geometries()
        if project:
            self.vehicle_definitions.update(project.vehicles)
            for vehicle_id in project.vehicles:
                self.vehicle_geometries.pop(vehicle_id, None)
            self.vehicle_geometries.update(project.geometries)
        self.vehicle_combo.blockSignals(True)
        self.vehicle_combo.clear()
        for vehicle in self.vehicle_definitions.values():
            self.vehicle_combo.addItem(vehicle.name, vehicle.id)
        self.vehicle_combo.blockSignals(False)
        if selected not in self.vehicle_definitions:
            selected = self.vehicle_combo.itemData(0)
        self.swept_paths = project.paths if project else [
            SweptPathDocument(uuid.uuid4().hex, "Swept path 1", selected, "#f28583", [])
        ]
        chosen = project.active_id if project else self.swept_paths[0].id
        self.active_path_id = chosen
        self._refresh_path_picker()
        self._hover_point = None
        self.view.drawing_mode = False
        self.draw_action.setChecked(False)
        self.finish_action.setEnabled(False)
        unit_code = int(document.header.get("$INSUNITS", 0) or 0)
        self.units_spin.setValue(project.units if project else self._units_per_metre(unit_code))
        if project:
            for key, checkbox in self._display_controls().items():
                checkbox.blockSignals(True)
                checkbox.setChecked(project.display.get(key, True))
                checkbox.blockSignals(False)
            saved_layers = set(project.clearance_layers)
            for i in range(self.clearance_layer_list.count()):
                item = self.clearance_layer_list.item(i)
                item.setCheckState(
                    Qt.CheckState.Checked if item.text() in saved_layers else Qt.CheckState.Unchecked
                )
        self.active_path_id = ""
        self._path_selection_changed(self.path_combo.currentIndex())
        self.draw_action.setEnabled(True)
        self.setWindowTitle(f"{path.name} | OpenArch Swept Paths")
        path_count = len(self.swept_paths)
        path_label = "path" if path_count == 1 else "paths"
        self.status.showMessage(f"{path.name} · {count:,} drawing entities · {path_count} {path_label}")
        self._show_editor()
        self.view.fit_drawing()
        self._saved_state = self._edit_state()
        self.undo_stack.clear()
        self._history_current = self._capture_history()
        if warnings:
            QMessageBox.warning(self, "Paths requiring review", "\n".join(warnings))

    def _capture_history(self):
        return EditorState(
            tuple(PathState(
                p.id, p.name, p.vehicle_id, p.color, tuple(p.points),
                tuple(p.vehicle_stations),
                self._settings() if p.id == self.active_path_id else p.settings
            ) for p in self.swept_paths),
            self.active_path_id, self.units_spin.value(),
            dict(self.vehicle_definitions), dict(self.vehicle_geometries),
            tuple(c.isChecked() for c in self._display_controls().values())
        )

    def _record_edit(self, label):
        if self.document is None or self._history_restoring:
            return
        after = self._capture_history()
        before = self._history_current
        self._history_current = after
        if before is not None and before != after:
            self.undo_stack.push(EditCommand(self, label, before, after))

    def _restore_history(self, state):
        drawing = self.view.drawing_mode and state.active_id == self.active_path_id
        previous_paths = {p.id: p for p in self.swept_paths}
        previous_geometry = self.vehicle_geometries
        previous_units = self.units_spin.value()
        self.active_path.settings = self._settings()
        self._history_restoring = True
        self.preview_timer.stop()
        self._preview_generation += 1
        self._path_switching = True
        self.vehicle_definitions = dict(state.vehicles)
        self.vehicle_geometries = dict(state.geometries)
        self.vehicle_combo.blockSignals(True)
        self.vehicle_combo.clear()
        for vehicle in self.vehicle_definitions.values():
            self.vehicle_combo.addItem(vehicle.name, vehicle.id)
        self.vehicle_combo.blockSignals(False)
        self.units_spin.setValue(state.units)
        for checkbox, visible in zip(self._display_controls().values(), state.display):
            checkbox.blockSignals(True)
            checkbox.setChecked(visible)
            checkbox.blockSignals(False)
        self.swept_paths = [
            SweptPathDocument(p.id, p.name, p.vehicle_id, p.color, list(p.points),
                              simulation_scale=state.units, vehicle_stations=list(p.stations),
                              settings=p.settings) for p in state.paths
        ]
        for index, restored in enumerate(self.swept_paths):
            previous = previous_paths.get(restored.id)
            if (previous is not None and previous_units == state.units
                    and previous.points == restored.points
                    and previous.settings == restored.settings
                    and previous.vehicle_id == restored.vehicle_id
                    and previous_geometry.get(previous.vehicle_id) == state.geometries.get(restored.vehicle_id)):
                self.swept_paths[index] = replace(
                    restored, simulation_result=previous.simulation_result,
                    envelope_geometry=previous.envelope_geometry,
                    detailed=previous.detailed
                )
        self.active_path_id = state.active_id
        self._refresh_path_picker()
        self.active_path_id = ""
        self._path_selection_changed(self.path_combo.currentIndex())
        # Background items are never recreated by an edit.
        for item in self.view.scene.items():
            group = item.data(0)
            checkbox = {"outside": self.outside_visible, "vehicle": self.vehicle_visible,
                        "wheel": self.wheels_visible}.get(group)
            if checkbox is not None:
                item.setVisible(checkbox.isChecked())
        if drawing:
            self.draw_action.setChecked(True)
            self.set_drawing_mode(True)
        self._history_current = self._capture_history()
        self._history_restoring = False

    def _display_controls(self):
        return {name: getattr(self, name) for name in (
            "outside_visible", "vehicle_visible", "wheels_visible", "show_start_position", "show_end_position"
        )}

    def _edit_state(self):
        active = self._settings()
        return (self.units_spin.value(), tuple(
            (p.id, p.name, p.vehicle_id, p.color, tuple(p.points), tuple(p.vehicle_stations),
             active if p.id == self.active_path_id else p.settings,
             self.vehicle_geometries.get(p.vehicle_id)) for p in self.swept_paths
        ), tuple(c.isChecked() for c in self._display_controls().values()))

    def _confirm_replace(self):
        if self.document is None or self._edit_state() == self._saved_state:
            return True
        buttons = QMessageBox.StandardButton
        choice = QMessageBox.question(self, "Save project changes?", "Save changes before closing this drawing?",
                                      buttons.Save | buttons.Discard | buttons.Cancel, buttons.Save)
        return choice == buttons.Discard or (choice == buttons.Save and self.save_editable_project())

    def save_editable_project(self, _checked=False, *, save_as=False):
        if self.document is None:
            return False
        target = self.project_path
        if target is None or save_as:
            filename, _ = QFileDialog.getSaveFileName(self, "Save editable project",
                str((target or self.drawing_path).with_suffix(".oasp")), "OpenArch projects (*.oasp)")
            if not filename:
                return False
            target = Path(filename)
            if target.suffix.lower() != ".oasp":
                target = target.with_suffix(".oasp")
        self.active_path.settings = self._settings()
        project = Project(self.swept_paths, self.active_path_id, self.units_spin.value(),
                          self.vehicle_definitions, self.vehicle_geometries,
                          {k: c.isChecked() for k, c in self._display_controls().items()},
                          [self.clearance_layer_list.item(i).text() for i in range(self.clearance_layer_list.count())
                           if self.clearance_layer_list.item(i).checkState() == Qt.CheckState.Checked])
        try:
            save_project(target, self._background_file, project)
        except Exception as error:
            QMessageBox.critical(self, "Could not save project", str(error))
            return False
        self.project_path = target
        self.undo_stack.setClean()
        self._saved_state = self._edit_state()
        self.setWindowTitle(f"{target.name} | OpenArch Swept Paths")
        self.status.showMessage(f"Project saved to {target}")
        return True

    @staticmethod
    def _units_per_metre(unit_code: int) -> float:
        return {
            0: 1.0,
            1: 39.37007874015748,  # inches
            2: 3.280839895013123,  # feet
            4: 1000.0,  # millimetres
            5: 100.0,  # centimetres
            6: 1.0,  # metres
            7: 0.001,  # kilometres
            10: 1.093613298337708,  # yards
        }.get(unit_code, 1.0)

    def _selected_vehicle(self) -> VehicleDefinition:
        vehicle_id = str(self.vehicle_combo.currentData())
        return self.vehicle_definitions[vehicle_id]

    @undoable("Change vehicle")
    def _vehicle_selected(self, _index: int = 0) -> None:
        vehicle = self._selected_vehicle()
        if not self._path_switching:
            self.active_path.vehicle_id = vehicle.id
        has_geometry = vehicle.id in self.vehicle_geometries
        self.minimum_radius_button.setEnabled(has_geometry)
        if (
            not self._path_switching
            and self._last_vehicle_geometry_mode is not None
            and self._last_vehicle_geometry_mode != has_geometry
            and self.route_points
        ):
            self.clear_route()
        self._last_vehicle_geometry_mode = has_geometry
        if hasattr(self, "draw_action"):
            self.draw_action.setText(
                "Draw steer-axle path" if has_geometry else "Draw reference path"
            )
        self.draw_action.setIconText("Draw path")
        self.length_spin.setEnabled(not has_geometry)
        self.width_spin.setEnabled(not has_geometry)
        if has_geometry:
            profile = self.vehicle_geometries[vehicle.id]
            self.length_spin.setValue(profile.overall_length_m)
            self.width_spin.setValue(profile.overall_width_m)
            self.steering_limit_spin.setValue(profile.max_steer_deg)
        else:
            self.length_spin.setValue(vehicle.length_m)
            self.width_spin.setValue(vehicle.width_m)
        self.steering_limit_spin.setEnabled(has_geometry)
        if urlsplit(vehicle.source_url).scheme in ("http", "https"):
            source = f'<a style="color:#9bcdbd" href="{escape(vehicle.source_url)}">{escape(vehicle.source)}</a>'
            self.vehicle_source_label.setText(source)
            self.vehicle_source_label.setOpenExternalLinks(True)
        else:
            self.vehicle_source_label.setText(escape(vehicle.source))

        self.save_vehicle_button.setEnabled(vehicle.supports_preview)
        self.edit_geometry_button.setEnabled(True)
        if has_geometry:
            self.vehicle_note_label.setText(
                "Simulation uses this explicit axle and coupling geometry. "
                "Review the source notes and verify all dimensions, steering limits "
                "and coupling stations for the project vehicle. "
                f"Source: {self.vehicle_geometries[vehicle.id].source_reference}. "
                "The solver rate-limits steering; compare the target and actual paths "
                "and watch for saturation."
            )
        elif vehicle.supports_preview:
            self.vehicle_note_label.setText(
                "Legacy preview: a rigid rectangle follows the drawn body-centre path. "
                "Edit axle geometry to simulate steer angle, wheel paths and off-tracking. "
                "Confirm all dimensions against the project vehicle source."
            )
        else:
            self.vehicle_note_label.setText(
                "This combination needs verified tractor, trailer, axle and coupling geometry. "
                "Edit the geometry profile to enable articulated simulation. " + vehicle.notes
            )
        self._clear_envelope(clear_record=not self._path_switching)
        self._update_export_state()
        if not self._path_switching:
            self._queue_live_preview()

    @undoable("Change path settings")
    def _vehicle_dimensions_changed(self, _value: float = 0.0) -> None:
        if self._path_switching:
            return
        self.active_path.settings = self._settings()
        self.active_path.clearance_summary = None
        self._clear_envelope()
        self._render_other_paths()
        self._update_export_state()
        self._queue_live_preview()

    @undoable("Change drawing scale")
    def _drawing_scale_changed(self):
        if not self._path_switching:
            for path in self.swept_paths:
                path.simulation_result = None
                path.envelope_geometry = None
                path.simulation_scale = self.units_spin.value()
            self._vehicle_dimensions_changed()

    def _settings(self) -> PathSettings:
        return PathSettings(**{
            field.name: (
                self.dry_steering_check.isChecked() if field.name == "dry_steering"
                else self.reverse_action.isChecked() if field.name == "reverse"
                else getattr(self, field.name + "_spin").value()
            ) for field in fields(PathSettings)
        })

    def _apply_settings(self, settings):
        if settings is not None:
            for field in fields(settings):
                if field.name == "dry_steering":
                    self.dry_steering_check.setChecked(settings.dry_steering)
                elif field.name == "reverse":
                    self.reverse_action.setChecked(settings.reverse)
                else:
                    getattr(self, field.name + "_spin").setValue(getattr(settings, field.name))

    def _sync_direction_action(self) -> None:
        self.reverse_action.blockSignals(True)
        reverse = bool(self.active_path.settings and self.active_path.settings.reverse)
        self.reverse_action.setChecked(reverse)
        self.reverse_action.setText("Direction: Reverse" if reverse else "Direction: Forward")
        self.reverse_action.blockSignals(False)

    @undoable("Change travel direction")
    def _direction_toggled(self, reverse: bool) -> None:
        self.reverse_action.setText("Direction: Reverse" if reverse else "Direction: Forward")
        if self._path_switching:
            return
        self.active_path.settings = replace(self._settings(), reverse=reverse)
        self._vehicle_dimensions_changed()

    @undoable("Use minimum turn radius")
    def _use_minimum_turn_radius(self) -> None:
        geometry = self.vehicle_geometries.get(self._selected_vehicle().id)
        angle = math.radians(self.steering_limit_spin.value())
        if geometry is None or angle <= 0:
            self.status.showMessage("Minimum radius needs axle geometry and a positive steering angle")
            return
        radius = geometry.steer_axle_m / math.tan(angle)
        self.dry_steering_check.setChecked(True)
        self.corner_radius_spin.setValue(min(100.0, max(0.1, radius)))
        self.status.showMessage(f"Dry steering enabled · waypoint radius set to {radius:.2f} m")

    def _queue_live_preview(self) -> None:
        self._preview_generation += 1
        self._preview_show_errors = False
        self._preview_fast = self.view.drawing_mode
        if len(self._current_preview_route()) >= 2 and self.document is not None:
            self.preview_timer.start()
        else:
            self.preview_timer.stop()
        self._update_export_state()

    def _preview_current_route(self) -> None:
        route = self._current_preview_route()
        if len(route) >= 2 and not self._preview_running:
            self._start_preview(
                route, self._preview_generation, fast_preview=self._preview_fast
            )

    def _current_preview_route(self) -> list[tuple[float, float]]:
        route = list(self.route_points)
        if (
            self.view.drawing_mode
            and self._hover_point is not None
            and (not route or math.dist(route[-1], self._hover_point) > 1e-6)
        ):
            route.append(self._hover_point)
        return route

    def update_hover_point(self, scene_x: float, scene_y: float) -> None:
        if not self.view.drawing_mode:
            return
        point = (scene_x, scene_y)
        if self._hover_point is not None and math.dist(self._hover_point, point) < 1e-5:
            return
        self._hover_point = point
        self._preview_validity = "pending"
        self._render_hover_cursor()
        self._queue_live_preview()

    def _route_label(self) -> str:
        if self._selected_vehicle().id in self.vehicle_geometries:
            return "Steer-axle path"
        return "Reference path"

    @undoable("Edit vehicle geometry")
    def edit_vehicle_geometry(self) -> None:
        vehicle = self._selected_vehicle()
        dialog = GeometryDialog(
            vehicle.name,
            self.vehicle_geometries.get(vehicle.id),
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.geometry is None:
            return
        self.vehicle_geometries[vehicle.id] = dialog.geometry
        for path in self.swept_paths:
            if path.vehicle_id == vehicle.id:
                path.simulation_result = None
                path.envelope_geometry = None
        settings = QSettings("OpenArch", "OpenArchSweptPaths")
        settings.setValue(
            "vehicleGeometries",
            json.dumps(
                {key: value.to_dict() for key, value in self.vehicle_geometries.items()}
            ),
        )
        self._clear_envelope()
        self._vehicle_selected()
        self._render_other_paths()
        self.status.showMessage(f"Saved kinematic geometry for {vehicle.name}")

    @undoable("Save custom vehicle")
    def save_custom_vehicle(self) -> None:
        name, accepted = QInputDialog.getText(
            self, "Save custom vehicle", "Vehicle profile name:"
        )
        if not accepted or not name.strip():
            return
        vehicle_id = f"custom-{uuid.uuid4().hex[:12]}"
        vehicle = VehicleDefinition(
            vehicle_id,
            name.strip(),
            self.length_spin.value(),
            self.width_spin.value(),
            "rigid",
            "User-defined custom rigid vehicle",
            "",
            "Rigid-body preview only.",
        )
        self.vehicle_definitions[vehicle.id] = vehicle
        self.vehicle_combo.addItem(vehicle.name, vehicle.id)
        self.vehicle_combo.setCurrentIndex(self.vehicle_combo.count() - 1)
        settings = QSettings("OpenArch", "OpenArchSweptPaths")
        custom = [
            item.to_dict()
            for item in self.vehicle_definitions.values()
            if item.source == "User-defined custom rigid vehicle"
        ]
        settings.setValue("customVehicles", json.dumps(custom))
        self.status.showMessage(f"Saved custom vehicle: {vehicle.name}")

    def set_drawing_mode(self, enabled: bool) -> None:
        if enabled:
            self._stop_vehicle_station_mode()
        self.view.drawing_mode = enabled
        self.finish_action.setEnabled(enabled)
        if enabled:
            self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.view.setFocus()
            path_name = (
                "steer-axle path"
                if self._selected_vehicle().id in self.vehicle_geometries
                else "body-centre path"
            )
            self.status.showMessage(
                f"Move the cursor to preview the next {path_name} point, then click to add it · "
                "Esc, double-click or Finish path to finish"
            )
        else:
            self._preview_generation += 1
            self._hover_point = None
            self._render_hover_cursor()
            self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.status.showMessage("Path edit mode off · Drag nodes to edit · Middle-drag pans · Wheel zooms")
        self._update_export_state()
        if not enabled:
            self._queue_live_preview()

    @undoable("Add waypoint")
    def add_route_point(self, scene_x: float, scene_y: float) -> None:
        self._hover_point = None
        self._render_hover_cursor()
        if self.route_points and math.dist(self.route_points[-1], (scene_x, scene_y)) < 1e-6:
            return
        self.route_points.append((scene_x, scene_y))
        self.active_path.clearance_summary = None
        self._clear_envelope()
        self._render_route()
        self._update_export_state()
        self.status.showMessage(f"{self._route_label()}: {len(self.route_points)} points")
        self._queue_live_preview()

    def arm_waypoint_insert(self) -> None:
        if self.view.drawing_mode or self.view.position_mode:
            self.status.showMessage("Finish the current drawing or position tool before inserting a waypoint")
            return
        if len(self.route_points) < 2:
            self.status.showMessage("Define a route before inserting a waypoint")
            return
        self.view.insert_waypoint_mode = True
        self.view.setCursor(Qt.CursorShape.CrossCursor)
        self.status.showMessage("Click a route segment to insert a waypoint · Esc to cancel")

    @undoable("Insert waypoint")
    def insert_route_point(self, x: float, y: float) -> None:
        points = self.route_points
        if len(points) < 2:
            return
        best_index, best_distance = 1, float("inf")
        for index, (start, end) in enumerate(zip(points, points[1:]), start=1):
            dx, dy = end[0] - start[0], end[1] - start[1]
            length_squared = dx * dx + dy * dy
            fraction = 0.0 if length_squared <= 1e-15 else max(
                0.0, min(1.0, ((x - start[0]) * dx + (y - start[1]) * dy) / length_squared)
            )
            projected = (start[0] + fraction * dx, start[1] + fraction * dy)
            distance = (x - projected[0]) ** 2 + (y - projected[1]) ** 2
            if distance < best_distance:
                best_index, best_distance = index, distance
        self.route_points.insert(best_index, (x, y))
        self.active_path.clearance_summary = None
        self._clear_envelope()
        self._render_route()
        self._queue_live_preview()

    @undoable("Delete waypoints")
    def delete_selected_waypoints(self) -> None:
        selected = {
            item.index for item in self.view.scene.selectedItems()
            if isinstance(item, WaypointItem)
        }
        if not selected:
            return
        if len(self.route_points) - len(selected) < 2:
            self.status.showMessage("A route must retain at least two waypoints")
            return
        self.route_points[:] = [
            point for index, point in enumerate(self.route_points) if index not in selected
        ]
        self.active_path.clearance_summary = None
        self._clear_envelope()
        self._render_route()
        self._queue_live_preview()

    def finish_route(self) -> None:
        if self.view.position_mode:
            self._stop_vehicle_station_mode()
            self.status.showMessage("Vehicle position placement finished")
            return
        self.preview_timer.stop()
        self.draw_action.setChecked(False)
        self.set_drawing_mode(False)
        if len(self.route_points) < 2:
            self.status.showMessage("Add at least two points to define a path")
        else:
            self._preview_generation += 1
            self._preview_show_errors = False
            self.calculate_envelope(show_errors=False)
            if self.simulation_result is None and self.envelope_geometry is None:
                self.status.showMessage(
                    f"{self._route_label()} complete · {len(self.route_points)} points"
                )

    @undoable("Remove last waypoint")
    def undo_route_point(self) -> None:
        if not self.route_points:
            return
        self.route_points.pop()
        self.active_path.clearance_summary = None
        self._clear_envelope()
        self._render_route()
        self._update_export_state()
        self.status.showMessage(f"{self._route_label()}: {len(self.route_points)} points")
        self._queue_live_preview()

    @undoable("Clear path")
    def clear_route(self) -> None:
        self.preview_timer.stop()
        self._preview_generation += 1
        self._hover_point = None
        self._render_hover_cursor()
        self.route_points.clear()
        self.active_path.clearance_summary = None
        self.active_path.vehicle_stations.clear()
        self._refresh_vehicle_station_list()
        self._clear_envelope()
        self.draw_action.setChecked(False)
        self.set_drawing_mode(False)
        self.preview_timer.stop()
        self._preview_generation += 1
        self._render_route()
        self._update_export_state()
        self.status.showMessage("Reference path cleared")

    def _clear_envelope(self, *, clear_record: bool = True) -> None:
        self.playback_timer.stop()
        if hasattr(self, "playback_button"):
            self.playback_button.setText("Play route")
            self.playback_slider.setEnabled(False)
        for item in self.trace_items:
            self.view.scene.removeItem(item)
        self.trace_items.clear()
        self.position_items.clear()
        for item in self.envelope_items:
            self.view.scene.removeItem(item)
        self.envelope_items.clear()
        self.envelope_geometry = None
        self.simulation_result = None
        if clear_record:
            self.active_path.simulation_result = None
            self.active_path.envelope_geometry = None

    def _render_route(self) -> None:
        for item in self.route_items:
            self.view.scene.removeItem(item)
        self.route_items.clear()
        self.route_line_item = None
        if not self.route_points:
            return

        self._render_route_line()
        for index, (x, y) in enumerate(self.route_points):
            marker = WaypointItem(index, (x, y), self.active_path.color, self._move_waypoint)
            self.view.scene.addItem(marker)
            self.route_items.append(marker)

    def _render_route_line(self) -> None:
        if self.route_line_item is not None:
            self.view.scene.removeItem(self.route_line_item)
            if self.route_line_item in self.route_items:
                self.route_items.remove(self.route_line_item)
        if not self.route_points:
            self.route_line_item = None
            return
        path = QPainterPath()
        first_x, first_y = self.route_points[0]
        path.moveTo(first_x, -first_y)
        for x, y in self.route_points[1:]:
            path.lineTo(x, -y)
        pen = QPen(QColor(self.active_path.color))
        pen.setCosmetic(True)
        pen.setWidthF(2.5)
        line_item = QGraphicsPathItem(path)
        line_item.setPen(pen)
        line_item.setZValue(100)
        self.view.scene.addItem(line_item)
        self.route_items.append(line_item)
        self.route_line_item = line_item

    def _move_waypoint(self, index: int, x: float, y: float, live: bool) -> None:
        if not 0 <= index < len(self.route_points):
            return
        self.route_points[index] = (x, y)
        self.active_path.clearance_summary = None
        self._clear_envelope()
        if live:
            self._render_route_line()
        else:
            self._render_route()
        self._queue_live_preview()
        self._preview_fast = live
        if not live:
            self._record_edit("Move waypoint")

    def _render_other_paths(self) -> None:
        for item in self.other_path_items:
            self.view.scene.removeItem(item)
        self.other_path_items.clear()
        for path_document in self.swept_paths:
            if path_document.id == self.active_path_id:
                continue
            if path_document.envelope_geometry is not None:
                swept_shape = QPainterPath()
                for polygon in polygons(path_document.envelope_geometry):
                    self._add_ring(swept_shape, polygon.exterior.coords)
                    for interior in polygon.interiors:
                        self._add_ring(swept_shape, interior.coords)
                color = QColor(109, 186, 114, 42)
                envelope_item = QGraphicsPathItem(swept_shape)
                envelope_item.setPen(QPen(QColor("#6dba72"), 0))
                envelope_item.setBrush(QBrush(color))
                envelope_item.setZValue(15)
                self.view.scene.addItem(envelope_item)
                self._tag_display_item(envelope_item, "outside")
                self.other_path_items.append(envelope_item)
            if path_document.points:
                path = QPainterPath()
                path.moveTo(path_document.points[0][0], -path_document.points[0][1])
                for x, y in path_document.points[1:]:
                    path.lineTo(x, -y)
                pen = QPen(QColor(path_document.color))
                pen.setCosmetic(True)
                pen.setWidthF(1.8)
                pen.setStyle(Qt.PenStyle.DashLine)
                route_item = QGraphicsPathItem(path)
                route_item.setPen(pen)
                route_item.setZValue(25)
                route_item.setToolTip(path_document.name)
                self.view.scene.addItem(route_item)
                self.other_path_items.append(route_item)
                for point_index, (x, y) in enumerate(path_document.points):
                    node = QGraphicsEllipseItem(-4, -4, 8, 8)
                    node.setPos(x, -y)
                    node.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
                    node.setBrush(QColor(path_document.color))
                    node.setPen(QPen(QColor("#202326"), 1))
                    node.setZValue(26)
                    node.setToolTip(f"{path_document.name} · node {point_index + 1}")
                    self.view.scene.addItem(node)
                    self.other_path_items.append(node)
                label = QGraphicsSimpleTextItem(
                    self.vehicle_definitions[path_document.vehicle_id].name
                )
                label.setBrush(QColor(path_document.color))
                label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
                label.setPos(path_document.points[0][0], -path_document.points[0][1])
                label.setZValue(27)
                label.setToolTip(path_document.name)
                self.view.scene.addItem(label)
                self.other_path_items.append(label)

    def _render_hover_cursor(self) -> None:
        for item in self.hover_items:
            self.view.scene.removeItem(item)
        self.hover_items.clear()
        if not self.view.drawing_mode or self._hover_point is None:
            return

        x, y = self._hover_point
        if self.route_points:
            path = QPainterPath()
            last_x, last_y = self.route_points[-1]
            path.moveTo(last_x, -last_y)
            path.lineTo(x, -y)
            pen = QPen(QColor("#9eaaa6"))
            pen.setCosmetic(True)
            pen.setWidthF(1.4)
            pen.setStyle(Qt.PenStyle.DashLine)
            segment = QGraphicsPathItem(path)
            segment.setPen(pen)
            segment.setZValue(102)
            self.view.scene.addItem(segment)
            self.hover_items.append(segment)

        half = 9.0
        cross = QPainterPath()
        cross.moveTo(-half, -half)
        cross.lineTo(half, half)
        cross.moveTo(-half, half)
        cross.lineTo(half, -half)
        color = {
            "valid": "#238b70",
            "invalid": "#d94740",
            "pending": "#c69332",
        }.get(self._preview_validity, "#c69332")
        pen = QPen(QColor(color))
        pen.setCosmetic(True)
        pen.setWidthF(2.6)
        marker = QGraphicsPathItem(cross)
        marker.setPos(x, -y)
        marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        marker.setPen(pen)
        marker.setZValue(110)
        marker.setToolTip(
            "Candidate path point · "
            + ("physically unreachable" if self._preview_validity == "invalid" else self._preview_validity)
        )
        self.view.scene.addItem(marker)
        self.hover_items.append(marker)

    def calculate_envelope(
        self, _checked: bool = False, *, show_errors: bool = True
    ) -> None:
        route = self._current_preview_route()
        if len(route) < 2:
            return
        vehicle = self._selected_vehicle()
        geometry = self.vehicle_geometries.get(vehicle.id)
        if geometry is None and not vehicle.supports_preview:
            message = "Configure the unit, axle and coupling geometry for this combination first."
            if show_errors:
                QMessageBox.information(self, "Vehicle geometry needed", message)
            else:
                self.status.showMessage(f"Preview unavailable · {message}")
            return
        self.preview_timer.stop()
        self._preview_generation += 1
        self._preview_show_errors = show_errors
        self._preview_fast = False
        self._start_preview(route, self._preview_generation, fast_preview=False)

    def _start_preview(
        self,
        route: list[tuple[float, float]],
        token: int,
        *,
        fast_preview: bool,
    ) -> None:
        if self._preview_running:
            return
        vehicle = self._selected_vehicle()
        geometry = self.vehicle_geometries.get(vehicle.id)
        if geometry is None and not vehicle.supports_preview:
            self._preview_validity = "invalid"
            self._render_hover_cursor()
            self.status.showMessage("Preview unavailable · vehicle geometry is required")
            return
        scale = self.units_spin.value()
        self._clear_envelope()
        worker = _PreviewWorker(token, route, geometry, self._settings(), scale, fast_preview)
        worker.signals.finished.connect(self._preview_completed)
        self._preview_running = True
        self._update_export_state()
        self.preview_pool.start(worker)

    def _preview_completed(self, token: int, payload: object, error: str) -> None:
        self._preview_running = False
        self._update_export_state()
        if token != self._preview_generation:
            if len(self._current_preview_route()) >= 2:
                self.preview_timer.start(0)
                self._update_export_state()
            return

        if error:
            self._clearance_pending = False
            self._clear_envelope()
            self._preview_validity = "invalid"
            self._render_hover_cursor()
            self.status.showMessage(f"Candidate path is unreachable · {error}")
            if self._preview_show_errors:
                QMessageBox.warning(self, "Could not calculate preview", error)
        else:
            result, envelope = payload
            self.envelope_geometry = envelope
            self.active_path.detailed = not self._preview_fast
            self.active_path.simulation_result = result
            self.active_path.envelope_geometry = envelope
            self.active_path.simulation_scale = self.units_spin.value()
            self._render_envelope(envelope)
            if result is None:
                self.simulation_result = None
                self.playback_slider.setEnabled(False)
                vehicle = self._selected_vehicle()
                self.status.showMessage(
                    f"Rigid-body footprint preview · {vehicle.name} · "
                    f"{self.length_spin.value():.2f} m × {self.width_spin.value():.2f} m"
                )
                self._preview_validity = "valid"
            else:
                self.simulation_result = result
                self.playback_slider.setEnabled(True)
                self.playback_slider.setValue(0)
                self.simulation_scale = self.units_spin.value()
                geometry = self.vehicle_geometries[self._selected_vehicle().id]
                self._render_simulation_traces(result, geometry, self.simulation_scale)
                self._render_current_vehicle(result, geometry, self.simulation_scale)
                max_steer = max(abs(value) for value in result.steering_angles_deg)
                max_wheel_steer = max(
                    (abs(angle) for pair in result.front_wheel_angles_deg for angle in pair),
                    default=0.0,
                )
                max_offtracking = max(result.offtracking_by_axle_m.values(), default=0.0)
                reachable = (
                    result.path_complete
                    and not result.steering_rate_saturated
                    and result.maximum_articulation_deg <= geometry.max_articulation_deg
                    and result.maximum_path_error_m <= max(0.5, self.corner_radius_spin.value() * 0.1)
                )
                self._preview_validity = "valid" if reachable else "invalid"
                state = "" if reachable else " · candidate exceeds steering/path limits"
                self.status.showMessage(
                    f"{'Fast live preview' if self._preview_fast else 'Detailed preview'} · {geometry.name} · "
                    f"{'Reverse' if self.active_path.settings and self.active_path.settings.reverse else 'Forward'} · "
                    f"max wheel steer {max_wheel_steer:.1f}° (centre {max_steer:.1f}°) · "
                    f"axle off-tracking {max_offtracking:.2f} m · "
                    f"articulation {result.maximum_articulation_deg:.1f}° · "
                    f"{'dry steering' if result.dry_steering else f'steering rate {result.max_steering_rate_deg_s:.1f}°/s'} · "
                    f"target path error {result.maximum_path_error_m:.2f} m{state}"
                )
            self._render_hover_cursor()
            if self._clearance_pending:
                self._run_clearance_check()

        if token != self._preview_generation and len(self._current_preview_route()) >= 2:
            self.preview_timer.start(0)
            self._update_export_state()

    def _render_simulation_traces(
        self, result: SimulationResult, geometry: CombinationGeometry, scale: float
    ) -> None:
        colors = ("#f2f4f5", "#c9d0d5", "#aeb7be", "#e3e7eb")

        def add_trace(
            points: Sequence[tuple[float, float]],
            color: str,
            width: float,
            *,
            dashed: bool = False,
            group: str = "vehicle",
        ) -> None:
            if len(points) < 2:
                return
            if len(points) > 1000:
                stride = math.ceil((len(points) - 1) / 999)
                reduced = list(points[::stride])
                if reduced[-1] != points[-1]:
                    reduced.append(points[-1])
                points = reduced
            path = QPainterPath()
            path.moveTo(points[0][0] * scale, -points[0][1] * scale)
            for x, y in points[1:]:
                path.lineTo(x * scale, -y * scale)
            pen = QPen(QColor(color))
            pen.setCosmetic(True)
            pen.setWidthF(width)
            if dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            item = QGraphicsPathItem(path)
            item.setPen(pen)
            item.setZValue(40)
            self.view.scene.addItem(item)
            self._tag_display_item(item, group)
            self.trace_items.append(item)

        # Show the target path in coral and the rate-limited actual path in a bright neutral.
        add_trace(result.target_steer_axle_path, "#f28583", 2.0, dashed=True)
        add_trace(result.steer_axle_path, "#e3e7eb", 2.4)
        for unit_index, unit in enumerate(result.units):
            color = colors[unit_index % len(colors)]
            unit_geometry = geometry.units[unit_index]
            for longitudinal in (
                unit_geometry.front_extent_m,
                -unit_geometry.rear_extent_m,
            ):
                for side in (-1, 1):
                    edge = []
                    for reference, heading in zip(
                        unit.reference_points, unit.headings_rad
                    ):
                        forward = (math.cos(heading), math.sin(heading))
                        normal = (-math.sin(heading), math.cos(heading))
                        edge.append(
                            (
                                reference[0] + forward[0] * longitudinal + normal[0] * side * unit_geometry.width_m / 2,
                                reference[1] + forward[1] * longitudinal + normal[1] * side * unit_geometry.width_m / 2,
                            )
                        )
                    add_trace(edge, color, 1.0)
            for axle_path in unit.axle_traces.values():
                add_trace(axle_path, color, 1.3)
            for wheel_path in unit.wheel_traces.values():
                add_trace(wheel_path, "#e24a4a", 0.8, group="wheel")

    def _tag_display_item(self, item: QGraphicsItem, group: str) -> None:
        item.setData(0, group)
        checkbox = {
            "outside": self.outside_visible,
            "vehicle": self.vehicle_visible,
            "wheel": self.wheels_visible,
        }[group]
        item.setVisible(checkbox.isChecked())

    def _render_current_vehicle(
        self, result: SimulationResult, geometry: CombinationGeometry, scale: float
    ) -> None:
        self._render_travel_positions(result, geometry, scale)

    def _render_travel_positions(
        self, result: SimulationResult, geometry: CombinationGeometry, scale: float,
        *, playback_fraction: float | None = None,
    ) -> None:
        for item in self.position_items:
            self.view.scene.removeItem(item)
            if item in self.trace_items:
                self.trace_items.remove(item)
        self.position_items.clear()
        if not result.units or not result.steer_axle_path:
            return

        last_index = len(result.steer_axle_path) - 1
        positions: list[tuple[int, str]] = []
        if self.show_start_position.isChecked():
            positions.append((0, "Start"))
        if self.show_end_position.isChecked():
            positions.append((last_index, "End"))
        for station_index, fraction in enumerate(self.active_path.vehicle_stations, start=1):
            positions.append((round(fraction * last_index), f"Position {station_index}"))
        if playback_fraction is not None:
            positions.append((round(playback_fraction * last_index), "Playback"))

        colors = ("#f2f4f5", "#c9d0d5", "#aeb7be", "#e3e7eb")
        vehicle_name = self.vehicle_definitions[self.active_path.vehicle_id].name
        for pose_index, position_name in positions:
            poses: list[tuple[tuple[float, float], float]] = []
            for unit_index, (trajectory, unit_geometry) in enumerate(zip(result.units, geometry.units)):
                reference = trajectory.reference_points[pose_index]
                heading = trajectory.headings_rad[pose_index]
                poses.append((reference, heading))
                forward = (math.cos(heading), math.sin(heading))
                normal = (-math.sin(heading), math.cos(heading))

                def world(longitudinal: float, lateral: float) -> tuple[float, float]:
                    return (reference[0] + forward[0] * longitudinal + normal[0] * lateral,
                            reference[1] + forward[1] * longitudinal + normal[1] * lateral)

                corners = [
                    world(unit_geometry.front_extent_m, unit_geometry.width_m / 2),
                    world(unit_geometry.front_extent_m, -unit_geometry.width_m / 2),
                    world(-unit_geometry.rear_extent_m, -unit_geometry.width_m / 2),
                    world(-unit_geometry.rear_extent_m, unit_geometry.width_m / 2),
                ]
                shape = QPainterPath()
                shape.moveTo(corners[0][0] * scale, -corners[0][1] * scale)
                for x, y in corners[1:]:
                    shape.lineTo(x * scale, -y * scale)
                shape.closeSubpath()
                body = QGraphicsPathItem(shape)
                body.setBrush(QBrush(QColor(20, 22, 24, 165)))
                pen = QPen(QColor(colors[unit_index % len(colors)]))
                pen.setCosmetic(True)
                pen.setWidthF(2.0)
                body.setPen(pen)
                body.setZValue(62)
                body.setToolTip(f"{position_name} · {unit_geometry.name}")
                self.view.scene.addItem(body)
                self._tag_display_item(body, "vehicle")
                self.trace_items.append(body)
                self.position_items.append(body)

                steer_angles = result.front_wheel_angles_deg[pose_index] if unit_index == 0 else (0.0, 0.0)
                for axle_offset in unit_geometry.axle_offsets_m:
                    is_steer = unit_index == 0 and math.isclose(
                        axle_offset, geometry.steer_axle_m, abs_tol=1e-6
                    )
                    for side, angle in zip((1, -1), steer_angles):
                        wheel_heading = heading + (math.radians(angle) if is_steer else 0.0)
                        wf = (math.cos(wheel_heading), math.sin(wheel_heading))
                        wn = (-wf[1], wf[0])
                        center = world(axle_offset, side * unit_geometry.track_m / 2)
                        wheel_corners = [
                            (center[0] + wf[0] * along + wn[0] * across,
                             center[1] + wf[1] * along + wn[1] * across)
                            for along, across in ((0.48, 0.12), (0.48, -0.12), (-0.48, -0.12), (-0.48, 0.12))
                        ]
                        wheel_shape = QPainterPath()
                        wheel_shape.moveTo(wheel_corners[0][0] * scale, -wheel_corners[0][1] * scale)
                        for x, y in wheel_corners[1:]:
                            wheel_shape.lineTo(x * scale, -y * scale)
                        wheel_shape.closeSubpath()
                        wheel = QGraphicsPathItem(wheel_shape)
                        wheel.setBrush(QBrush(QColor("#17191b")))
                        wheel.setPen(QPen(QColor("#e24a4a"), 0))
                        wheel.setZValue(64)
                        wheel.setToolTip(f"{unit_geometry.name} wheel" + (f" · {angle:.1f}° steer" if is_steer else ""))
                        self.view.scene.addItem(wheel)
                        self._tag_display_item(wheel, "wheel")
                        self.trace_items.append(wheel)
                        self.position_items.append(wheel)

            for index in range(1, len(poses)):
                towing_reference, towing_heading = poses[index - 1]
                trailer_reference, trailer_heading = poses[index]
                towing = geometry.units[index - 1]
                trailer = geometry.units[index]
                p1 = (towing_reference[0] - math.cos(towing_heading) * towing.rear_hitch_m,
                      towing_reference[1] - math.sin(towing_heading) * towing.rear_hitch_m)
                p2 = (trailer_reference[0] + math.cos(trailer_heading) * trailer.front_hitch_m,
                      trailer_reference[1] + math.sin(trailer_heading) * trailer.front_hitch_m)
                coupling = QPainterPath()
                coupling.moveTo(p1[0] * scale, -p1[1] * scale)
                coupling.lineTo(p2[0] * scale, -p2[1] * scale)
                pen = QPen(QColor("#f2f4f5"))
                pen.setCosmetic(True)
                pen.setWidthF(1.5)
                pen.setStyle(Qt.PenStyle.DashLine)
                item = QGraphicsPathItem(coupling)
                item.setPen(pen)
                item.setZValue(63)
                self.view.scene.addItem(item)
                self._tag_display_item(item, "vehicle")
                self.trace_items.append(item)
                self.position_items.append(item)

            lead, (start_reference, start_heading) = geometry.units[0], poses[0]
            center_offset = (lead.front_extent_m - lead.rear_extent_m) / 2
            label_x = (start_reference[0] + math.cos(start_heading) * center_offset) * scale
            label_y = -(start_reference[1] + math.sin(start_heading) * center_offset) * scale
            label_text = f"{position_name} · {vehicle_name}" if position_name == "Start" else position_name
            label = QGraphicsSimpleTextItem(label_text)
            label.setBrush(QColor("#f2f4f5"))
            label.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
            label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            bounds = label.boundingRect()
            label.setPos(label_x - bounds.width() / 2, label_y - bounds.height() / 2)
            label.setZValue(65)
            label.setToolTip(f"{self.active_path.name} · {label_text}")
            self.view.scene.addItem(label)
            self._tag_display_item(label, "vehicle")
            self.trace_items.append(label)
            self.position_items.append(label)

    def _render_envelope(self, geometry: object) -> None:
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        for polygon in polygons(geometry):
            self._add_ring(path, polygon.exterior.coords)
            for interior in polygon.interiors:
                self._add_ring(path, interior.coords)

        envelope = QGraphicsPathItem(path)
        envelope.setPen(QPen(QColor("#6dba72"), 0))
        envelope.setBrush(QBrush(QColor(109, 186, 114, 76)))
        envelope.setZValue(50)
        self.view.scene.addItem(envelope)
        self._tag_display_item(envelope, "outside")
        self.envelope_items.append(envelope)

    @staticmethod
    def _add_ring(path: QPainterPath, coordinates: object) -> None:
        points = list(coordinates)
        if len(points) < 4:
            return
        first = points[0]
        path.moveTo(float(first[0]), -float(first[1]))
        for point in points[1:-1]:
            path.lineTo(float(point[0]), -float(point[1]))
        path.closeSubpath()

    def _update_export_state(self) -> None:
        has_path = (
            self.document is not None
            and any(len(path.points) >= 2 for path in self.swept_paths)
            and not self.view.drawing_mode
            and not self._preview_running
            and not self.preview_timer.isActive()
        )
        self.project_action.setEnabled(self.document is not None)
        self.project_as_action.setEnabled(self.document is not None)
        self.export_action.setEnabled(has_path)
        self.save_copy_action.setEnabled(has_path)
        vehicle = self._selected_vehicle()
        can_calculate = (
            self.document is not None
            and len(self.route_points) >= 2
            and not self.view.drawing_mode
            and not self._preview_running
            and not self.preview_timer.isActive()
            and (
                vehicle.supports_preview or vehicle.id in self.vehicle_geometries
            )
        )
        self.calculate_action.setEnabled(can_calculate)

    def _save_route(self, target: str, include_base: bool) -> None:
        if self.drawing_path and Path(target).resolve() == self.drawing_path.resolve():
            raise ValueError("Choose a new output file so the source drawing is preserved.")
        if include_base and self.drawing_path:
            output = read_dxf(self._background_file or self.drawing_path)
        else:
            output = ezdxf.new("R2010")
            if self.document is not None:
                output.header["$INSUNITS"] = self.document.header.get("$INSUNITS", 0)

        modelspace = output.modelspace()
        for path_index, path_document in enumerate(self.swept_paths, start=1):
            if len(path_document.points) >= 2:
                vehicle = self.vehicle_definitions[path_document.vehicle_id]
                geometry = self.vehicle_geometries.get(vehicle.id)
                if (path_document.envelope_geometry is None or not path_document.detailed) and (
                    geometry or vehicle.supports_preview
                ):
                    settings = self._settings() if path_document is self.active_path else path_document.settings
                    path_document.simulation_result, path_document.envelope_geometry = calculate_path(
                        path_document.points, geometry, settings, self.units_spin.value(), False
                    )
                    path_document.simulation_scale = self.units_spin.value()
                    path_document.detailed = True
                self._write_path_dxf(modelspace, output, path_document, path_index)
        output.saveas(target)

    def _write_path_dxf(
        self, modelspace: object, output: object, path_document: SweptPathDocument, index: int
    ) -> None:
        scale = path_document.simulation_scale
        prefix = f"OASP_P{index}"
        path_layer = f"OASP-Vehicle-P{index}-Steer"
        node_layer = f"{prefix}_NODES"
        outside_layer = f"OASP-Outside-P{index}"
        colors = (1, 4, 30, 6, 3, 5)
        layer_color = colors[(index - 1) % len(colors)]

        def ensure_layer(
            name: str,
            color: int,
            lineweight: int | None = None,
            visibility_group: str | None = None,
        ) -> None:
            if name not in output.layers:
                attrs: dict[str, object] = {"color": color}
                if lineweight is not None:
                    attrs["lineweight"] = lineweight
                output.layers.new(name, dxfattribs=attrs)
            if visibility_group is not None:
                checkbox = {
                    "outside": self.outside_visible,
                    "vehicle": self.vehicle_visible,
                    "wheel": self.wheels_visible,
                }[visibility_group]
                layer = output.layers.get(name)
                layer.on() if checkbox.isChecked() else layer.off()

        ensure_layer(path_layer, 7, 35, "vehicle")
        ensure_layer(node_layer, layer_color)
        ensure_layer(outside_layer, 3, 25, "outside")
        result = path_document.simulation_result
        steer_points = (
            [(x * scale, y * scale) for x, y in result.target_steer_axle_path]
            if result is not None
            else path_document.points
        )
        modelspace.add_lwpolyline(steer_points, dxfattribs={"layer": path_layer})
        if result is not None:
            actual_layer = f"OASP-Vehicle-P{index}-Actual-Steer"
            ensure_layer(actual_layer, 7, visibility_group="vehicle")
            modelspace.add_lwpolyline(
                [(x * scale, y * scale) for x, y in result.steer_axle_path],
                dxfattribs={"layer": actual_layer},
            )
        for x, y in path_document.points:
            modelspace.add_circle(
                (x, y), radius=max(0.15 * scale, 0.05), dxfattribs={"layer": node_layer}
            )

        if path_document.envelope_geometry is not None:
            for polygon in polygons(path_document.envelope_geometry):
                for coordinates in (
                    polygon.exterior.coords,
                    *(interior.coords for interior in polygon.interiors),
                ):
                    vertices = [(float(x), float(y)) for x, y in list(coordinates)[:-1]]
                    if len(vertices) >= 3:
                        modelspace.add_lwpolyline(
                            vertices,
                            close=True,
                            dxfattribs={"layer": outside_layer},
                        )

        vehicle = self.vehicle_definitions[path_document.vehicle_id]
        geometry = self.vehicle_geometries.get(path_document.vehicle_id)
        if result is None or geometry is None:
            return

        unit_layers: list[tuple[str, str]] = []
        for unit_index, (trajectory, unit_geometry) in enumerate(
            zip(result.units, geometry.units), start=1
        ):
            axle_layer = f"OASP-Vehicle-P{index}-Axle-U{unit_index}"
            wheel_layer = f"OASP-Wheel-P{index}-U{unit_index}"
            edge_layer = f"OASP-Vehicle-P{index}-Body-U{unit_index}"
            ensure_layer(axle_layer, 7, visibility_group="vehicle")
            ensure_layer(wheel_layer, 1, visibility_group="wheel")
            ensure_layer(edge_layer, 7, visibility_group="vehicle")
            unit_layers.append((wheel_layer, edge_layer))
            for points in trajectory.axle_traces.values():
                modelspace.add_lwpolyline(
                    [(x * scale, y * scale) for x, y in points],
                    dxfattribs={"layer": axle_layer},
                )
            for points in trajectory.wheel_traces.values():
                modelspace.add_lwpolyline(
                    [(x * scale, y * scale) for x, y in points],
                    dxfattribs={"layer": wheel_layer},
                )
            for longitudinal in (
                unit_geometry.front_extent_m,
                -unit_geometry.rear_extent_m,
            ):
                for side in (-1, 1):
                    edge = []
                    for (x, y), heading in zip(
                        trajectory.reference_points, trajectory.headings_rad
                    ):
                        edge.append(
                            (
                                (x - math.sin(heading) * side * unit_geometry.width_m / 2
                                 + math.cos(heading) * longitudinal) * scale,
                                (y + math.cos(heading) * side * unit_geometry.width_m / 2
                                 + math.sin(heading) * longitudinal) * scale,
                            )
                        )
                    modelspace.add_lwpolyline(edge, dxfattribs={"layer": edge_layer})

        pose_count = len(result.steer_axle_path)
        last_pose = pose_count - 1
        positions: list[tuple[int, str]] = []
        if self.show_start_position.isChecked():
            positions.append((0, "Start"))
        if self.show_end_position.isChecked():
            positions.append((last_pose, "End"))
        for station_index, fraction in enumerate(path_document.vehicle_stations, start=1):
            positions.append((round(fraction * last_pose), f"Position {station_index}"))
        unique_positions: list[tuple[int, str]] = []
        seen_pose_indices: set[int] = set()
        for pose_index, label in positions:
            if pose_index not in seen_pose_indices:
                unique_positions.append((pose_index, label))
                seen_pose_indices.add(pose_index)

        coupling_layer = f"OASP-Vehicle-P{index}-Coupling"
        if len(result.units) > 1:
            ensure_layer(coupling_layer, 7, visibility_group="vehicle")
        for position_number, (pose_index, label) in enumerate(unique_positions, start=1):
            block_name = f"OASP_VEHICLE_P{index}_{position_number}_{path_document.id[:8].upper()}"
            vehicle_block = output.blocks.new(block_name)
            lead_reference = result.units[0].reference_points[pose_index]
            lead_heading = result.units[0].headings_rad[pose_index]

            def block_point(point: tuple[float, float]) -> tuple[float, float]:
                dx = point[0] - lead_reference[0]
                dy = point[1] - lead_reference[1]
                return (
                    (math.cos(lead_heading) * dx + math.sin(lead_heading) * dy) * scale,
                    (-math.sin(lead_heading) * dx + math.cos(lead_heading) * dy) * scale,
                )

            poses: list[tuple[tuple[float, float], float]] = []
            for unit_index, (trajectory, unit_geometry) in enumerate(
                zip(result.units, geometry.units)
            ):
                reference = trajectory.reference_points[pose_index]
                heading = trajectory.headings_rad[pose_index]
                poses.append((reference, heading))
                forward = (math.cos(heading), math.sin(heading))
                normal = (-math.sin(heading), math.cos(heading))

                def world(longitudinal: float, lateral: float) -> tuple[float, float]:
                    return (
                        reference[0] + forward[0] * longitudinal + normal[0] * lateral,
                        reference[1] + forward[1] * longitudinal + normal[1] * lateral,
                    )

                body_layer = unit_layers[unit_index][1]
                wheel_layer = unit_layers[unit_index][0]
                corners = [
                    world(unit_geometry.front_extent_m, unit_geometry.width_m / 2),
                    world(unit_geometry.front_extent_m, -unit_geometry.width_m / 2),
                    world(-unit_geometry.rear_extent_m, -unit_geometry.width_m / 2),
                    world(-unit_geometry.rear_extent_m, unit_geometry.width_m / 2),
                ]
                vehicle_block.add_lwpolyline(
                    [block_point(point) for point in corners],
                    close=True,
                    dxfattribs={"layer": body_layer},
                )
                steer_angles = (
                    result.front_wheel_angles_deg[pose_index]
                    if unit_index == 0 and result.front_wheel_angles_deg
                    else (0.0, 0.0)
                )
                for axle_offset in unit_geometry.axle_offsets_m:
                    for side, angle in zip((1, -1), steer_angles):
                        is_steer = unit_index == 0 and math.isclose(
                            axle_offset, geometry.steer_axle_m, abs_tol=1e-6
                        )
                        wheel_heading = heading + (math.radians(angle) if is_steer else 0.0)
                        wf = (math.cos(wheel_heading), math.sin(wheel_heading))
                        wn = (-wf[1], wf[0])
                        center = world(axle_offset, side * unit_geometry.track_m / 2)
                        wheel_corners = [
                            (
                                center[0] + wf[0] * along + wn[0] * across,
                                center[1] + wf[1] * along + wn[1] * across,
                            )
                            for along, across in (
                                (0.48, 0.12),
                                (0.48, -0.12),
                                (-0.48, -0.12),
                                (-0.48, 0.12),
                            )
                        ]
                        vehicle_block.add_lwpolyline(
                            [block_point(point) for point in wheel_corners],
                            close=True,
                            dxfattribs={"layer": wheel_layer},
                        )
                if unit_index == 0:
                    center = world(
                        (unit_geometry.front_extent_m - unit_geometry.rear_extent_m) / 2,
                        0,
                    )
                    text = vehicle.name if label == "Start" else label
                    vehicle_block.add_text(
                        text,
                        dxfattribs={"height": max(1.0 * scale, 0.2), "layer": body_layer},
                    ).set_placement(block_point(center))

            for unit_index in range(1, len(poses)):
                towing_reference, towing_heading = poses[unit_index - 1]
                trailer_reference, trailer_heading = poses[unit_index]
                towing_geometry = geometry.units[unit_index - 1]
                trailer_geometry = geometry.units[unit_index]
                coupling_points = (
                    (
                        towing_reference[0] - math.cos(towing_heading) * towing_geometry.rear_hitch_m,
                        towing_reference[1] - math.sin(towing_heading) * towing_geometry.rear_hitch_m,
                    ),
                    (
                        trailer_reference[0] + math.cos(trailer_heading) * trailer_geometry.front_hitch_m,
                        trailer_reference[1] + math.sin(trailer_heading) * trailer_geometry.front_hitch_m,
                    ),
                )
                vehicle_block.add_lwpolyline(
                    [block_point(point) for point in coupling_points],
                    dxfattribs={"layer": coupling_layer},
                )

            modelspace.add_blockref(
                block_name,
                (lead_reference[0] * scale, lead_reference[1] * scale, 0),
                dxfattribs={
                    # Keep the insert on layer 0 so its vehicle and wheel geometry
                    # can be toggled independently through their own block layers.
                    "layer": "0",
                    "rotation": math.degrees(lead_heading),
                },
            )

    def export_path(self) -> None:
        if not any(len(path.points) >= 2 for path in self.swept_paths):
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "Export swept paths and envelopes", "swept_paths.dxf", "DXF drawings (*.dxf)"
        )
        if not target:
            return
        if Path(target).suffix.lower() != ".dxf":
            target = str(Path(target).with_suffix(".dxf"))
        try:
            self._save_route(target, include_base=False)
        except Exception as error:
            QMessageBox.critical(self, "Could not export DXF", str(error))
            return
        self.status.showMessage(f"Exported {len([p for p in self.swept_paths if len(p.points) >= 2])} swept paths to {target}")

    def save_drawing_copy(self) -> None:
        if not any(len(path.points) >= 2 for path in self.swept_paths):
            return
        default_name = (
            f"{self.drawing_path.stem}_with_swept_paths.dxf"
            if self.drawing_path
            else "site_with_swept_paths.dxf"
        )
        target, _ = QFileDialog.getSaveFileName(
            self,
            "Save drawing copy with swept paths",
            default_name,
            "DXF drawings (*.dxf)",
        )
        if not target:
            return
        if Path(target).suffix.lower() != ".dxf":
            target = str(Path(target).with_suffix(".dxf"))
        try:
            self._save_route(target, include_base=True)
        except Exception as error:
            QMessageBox.critical(self, "Could not save drawing copy", str(error))
            return
        self.status.showMessage(f"Saved drawing copy with swept paths to {target}")

    def export_analysis_report(self) -> None:
        if self.document is None or not self.swept_paths:
            QMessageBox.information(self, "Export analysis report", "Open a drawing and define a path first.")
            return
        source = self.project_path or self.drawing_path or Path.cwd()
        target, _ = QFileDialog.getSaveFileName(
            self, "Export path analysis report", str(source.with_suffix(".html")), "HTML report (*.html)"
        )
        if not target:
            return
        sections = []
        for path in self.swept_paths:
            vehicle = self.vehicle_definitions.get(path.vehicle_id)
            result = path.simulation_result
            details = [
                f"<b>Vehicle:</b> {escape(vehicle.name if vehicle else path.vehicle_id)}",
                f"<b>Direction:</b> {'Reverse' if path.settings and path.settings.reverse else 'Forward'}",
                f"<b>Waypoints:</b> {len(path.points)}",
                f"<b>Route stations:</b> {len(path.vehicle_stations)}",
                f"<b>Dry steering:</b> {'Allowed' if path.settings and path.settings.dry_steering else 'Not allowed'}",
            ]
            if result:
                details.extend([
                    f"<b>Tracked distance:</b> {result.path_length_m:.2f} m",
                    f"<b>Maximum articulation:</b> {result.maximum_articulation_deg:.1f}°",
                    f"<b>Maximum axle off-tracking:</b> {max(result.offtracking_by_axle_m.values(), default=0.0):.2f} m",
                    f"<b>Maximum steering angle:</b> {max((abs(a) for a in result.steering_angles_deg), default=0.0):.1f}°",
                    f"<b>Completed:</b> {'Yes' if result.path_complete else 'No'}",
                    f"<b>Steering rate saturated:</b> {'Yes' if result.steering_rate_saturated else 'No'}",
                    f"<b>Maximum tracking error:</b> {result.maximum_path_error_m:.2f} m",
                ])
            else:
                details.append("<b>Simulation:</b> No calculated result")
            clearance = getattr(path, "clearance_summary", None)
            if clearance:
                collision_names = ", ".join(escape(str(name)) for name in clearance.get("collision_layers", []))
                minimum = clearance.get("minimum_clearance_m")
                details.append(
                    f"<b>Clearance:</b> {('Intersection with ' + collision_names) if collision_names else (f'{minimum:.2f} m minimum' if minimum is not None else 'No supported geometry found')}"
                )
            else:
                details.append("<b>Clearance:</b> Not checked")
            sections.append(f"<section><h2>{escape(path.name)}</h2><p>{'<br>'.join(details)}</p></section>")
        generated = escape(datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"))
        drawing = escape(self.drawing_path.name if self.drawing_path else "Drawing")
        content = (
            "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            "<title>OpenArch Swept Paths Analysis</title><style>body{font:15px Segoe UI,Arial;background:#1b1d20;color:#e7eaed;max-width:900px;margin:40px auto;padding:0 24px}h1,h2{color:#8ecb91}section{border:1px solid #42484e;border-radius:8px;padding:8px 20px;margin:18px 0}p{line-height:1.8}</style>"
            f"<h1>OpenArch Swept Paths · Analysis</h1><p><b>Base drawing:</b> {drawing}<br><b>Generated:</b> {generated}</p>"
            + "".join(sections)
            + "<small>Clearance checks cover supported CAD entities on selected layers. Verify vehicle assumptions and source geometry before design decisions.</small>"
        )
        try:
            Path(target).write_text(content, encoding="utf-8")
        except OSError as error:
            QMessageBox.critical(self, "Could not export report", str(error))
            return
        self.status.showMessage(f"Exported analysis report to {target}")

    def closeEvent(self, event: object) -> None:
        if not self._confirm_replace():
            event.ignore()
            return
        if self._background_tempdir:
            self._background_tempdir.cleanup()
            self._background_tempdir = None
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("OpenArch Swept Paths")
    app.setWindowIcon(QIcon(str(asset_path("app-icon.svg"))))
    window = MainWindow()
    window.show()
    if len(sys.argv) > 1 and Path(sys.argv[1]).is_file():
        QTimer.singleShot(0, lambda: window.load_drawing(sys.argv[1]))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()






