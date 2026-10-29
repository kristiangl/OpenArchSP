"""Application entry point for OpenArch Swept Paths."""

from __future__ import annotations

import json
import math
import sys
import tempfile
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import ezdxf
from shapely.affinity import scale as scale_geometry
from PySide6.QtCore import QObject, QRunnable, QSettings, QSize, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QFont,
    QPen,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
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
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
    QHBoxLayout,
)

from openarchsp.drawing import convert_dwg_to_dxf, draw_document, read_dxf
from openarchsp.swept_path import polygons, swept_footprint
from openarchsp.standard_profiles import standard_vehicle_geometries
from openarchsp.vehicles import (
    CUSTOM_RIGID_VEHICLE,
    STANDARD_VEHICLES,
    VehicleDefinition,
)
from openarchsp.vehicle_kinematics import CombinationGeometry, SimulationResult, simulate_combination


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


class DrawingView(QGraphicsView):
    worldClicked = Signal(float, float)
    worldMoved = Signal(float, float)
    finishRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.drawing_mode = False
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
        if self.drawing_mode and event.button() == Qt.MouseButton.RightButton:
            self.finishRequested.emit()
            event.accept()
            return
        super().mousePressEvent(event)

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
        if event.key() == Qt.Key.Key_Escape and self.drawing_mode:
            self.finishRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class _PreviewSignals(QObject):
    finished = Signal(int, object, str)


class _PreviewWorker(QRunnable):
    """Calculate one drawing preview without blocking mouse interaction."""

    def __init__(
        self,
        token: int,
        route: list[tuple[float, float]],
        geometry: CombinationGeometry | None,
        *,
        fast_preview: bool,
        scale: float,
        corner_radius_m: float,
        spacing_m: float,
        speed_kmh: float,
        lock_to_lock_s: float,
        rigid_length: float,
        rigid_width: float,
    ) -> None:
        super().__init__()
        self.token = token
        self.route = route
        self.geometry = geometry
        self.fast_preview = fast_preview
        self.scale = scale
        self.corner_radius_m = corner_radius_m
        self.spacing_m = spacing_m
        self.speed_kmh = speed_kmh
        self.lock_to_lock_s = lock_to_lock_s
        self.rigid_length = rigid_length
        self.rigid_width = rigid_width
        self.signals = _PreviewSignals()

    def run(self) -> None:
        try:
            if self.geometry is not None:
                route_metres = [(x / self.scale, y / self.scale) for x, y in self.route]
                result = simulate_combination(
                    route_metres,
                    self.geometry,
                    corner_radius_m=self.corner_radius_m,
                    spacing_m=self.spacing_m,
                    speed_kmh=self.speed_kmh,
                    lock_to_lock_s=self.lock_to_lock_s,
                    integration_step_s=0.32 if self.fast_preview else 0.05,
                    envelope_pose_stride=4 if self.fast_preview else 1,
                )
                envelope = scale_geometry(
                    result.envelope,
                    xfact=self.scale,
                    yfact=self.scale,
                    origin=(0, 0),
                )
                payload = (result, envelope)
            else:
                envelope = swept_footprint(
                    self.route,
                    self.rigid_length * self.scale,
                    self.rigid_width * self.scale,
                    max(0.25 * self.scale, 1e-6),
                )
                payload = (None, envelope)
            self.signals.finished.emit(self.token, payload, "")
        except Exception as error:
            self.signals.finished.emit(self.token, None, str(error))


@dataclass
class SweptPathDocument:
    id: str
    name: str
    vehicle_id: str
    color: str
    points: list[tuple[float, float]]
    simulation_result: SimulationResult | None = None
    envelope_geometry: object | None = None
    simulation_scale: float = 1.0


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
        for label, callback in (
            ("Add tractor", self._add_tractor),
            ("Add trailer", self._add_trailer),
            ("Add converter dolly", self._add_dolly),
            ("Add axle station", self._add_axle),
            ("Remove axle station", self._remove_axle),
            ("Remove component", self._remove_unit),
            ("Move up", lambda: self._move_unit(-1)),
            ("Move down", lambda: self._move_unit(1)),
        ):
            button = QPushButton(label)
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
            "QStatusBar { background: #24272b; color: #b9c0c7; }"
            "QScrollBar:vertical, QScrollBar:horizontal { background: #1b1d20; width: 12px; height: 12px; }"
            "QScrollBar::handle { background: #4b535b; border-radius: 5px; min-height: 22px; }"
        )
        self.document = None
        self.drawing_path: Path | None = None
        self._dwg_tempdir: tempfile.TemporaryDirectory[str] | None = None
        self._converted_base_path: Path | None = None
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
        self.vehicle_geometries = self._load_vehicle_geometries()
        self._last_vehicle_geometry_mode: bool | None = None

        self.view = DrawingView()
        self.setCentralWidget(self.view)
        self.view.worldClicked.connect(self.add_route_point)
        self.view.worldMoved.connect(self.update_hover_point)
        self.view.finishRequested.connect(self.finish_route)

        self._build_vehicle_panel()
        self._build_actions()
        self.vehicle_combo.currentIndexChanged.connect(self._vehicle_selected)
        self.length_spin.valueChanged.connect(self._vehicle_dimensions_changed)
        self.width_spin.valueChanged.connect(self._vehicle_dimensions_changed)
        self.units_spin.valueChanged.connect(self._vehicle_dimensions_changed)
        self.corner_radius_spin.valueChanged.connect(self._vehicle_dimensions_changed)
        self.speed_spin.valueChanged.connect(self._vehicle_dimensions_changed)
        self.steering_limit_spin.valueChanged.connect(self._vehicle_dimensions_changed)
        self.lock_to_lock_spin.valueChanged.connect(self._vehicle_dimensions_changed)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(100)
        self.preview_timer.timeout.connect(self._preview_current_route)

        self.status = QStatusBar()
        self.status.showMessage("Open a DXF or DWG site drawing to begin")
        self.setStatusBar(self.status)
        self._vehicle_selected()

    @property
    def active_path(self) -> SweptPathDocument:
        return next(path for path in self.swept_paths if path.id == self.active_path_id)

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
        self.swept_paths.append(path)
        self._refresh_path_picker()
        self.path_combo.setCurrentIndex(self.path_combo.findData(path.id))

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
        self.preview_timer.stop()
        self._preview_generation += 1
        self._hover_point = None
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
        self._path_switching = False
        self._render_route()
        self._render_other_paths()
        if self.active_path.simulation_result and self.active_path.envelope_geometry is not None:
            self.simulation_result = self.active_path.simulation_result
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
        elif len(self.route_points) >= 2 and self.document is not None:
            self._queue_live_preview()
        self._update_export_state()

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

        self.vehicle_combo = QComboBox()
        for vehicle in self.vehicle_definitions.values():
            self.vehicle_combo.addItem(vehicle.name, vehicle.id)
        form.addRow("Design vehicle", self.vehicle_combo)

        self.length_spin = self._dimension_spin(100.0, 5.2)
        self.width_spin = self._dimension_spin(20.0, 1.9)
        self.units_spin = QDoubleSpinBox()
        self.units_spin.setRange(0.000001, 1_000_000_000)
        self.units_spin.setDecimals(6)
        self.units_spin.setSingleStep(1)
        self.units_spin.setValue(1)
        self.units_spin.setToolTip(
            "Drawing units corresponding to one metre. DXF $INSUNITS is used when available."
        )

        form.addRow("Length (m)", self.length_spin)
        form.addRow("Width (m)", self.width_spin)
        form.addRow("Drawing units per metre", self.units_spin)
        self.corner_radius_spin = self._dimension_spin(100.0, 12.5)
        self.corner_radius_spin.setToolTip(
            "Radius used to round path waypoints into tangential turns."
        )
        form.addRow("Waypoint turn radius (m)", self.corner_radius_spin)
        self.speed_spin = self._dimension_spin(100.0, 5.0)
        form.addRow("Travel speed (km/h)", self.speed_spin)
        self.steering_limit_spin = self._dimension_spin(60.0, 35.0)
        self.steering_limit_spin.setToolTip(
            "Maximum centre road-wheel angle used by the live steering simulation. "
            "This overrides the vehicle profile for the current run."
        )
        form.addRow("Maximum road-wheel angle (°)", self.steering_limit_spin)
        self.lock_to_lock_spin = self._dimension_spin(30.0, 6.0)
        self.lock_to_lock_spin.setToolTip(
            "Time for the road wheels to move from full lock one way to full lock the other."
        )
        form.addRow("Steering lock-to-lock (s)", self.lock_to_lock_spin)

        self.vehicle_source_label = QLabel()
        self.vehicle_source_label.setWordWrap(True)
        form.addRow("Source / model", self.vehicle_source_label)

        self.vehicle_note_label = QLabel()
        self.vehicle_note_label.setWordWrap(True)
        self.vehicle_note_label.setStyleSheet("color: #c1ad83;")
        form.addRow(self.vehicle_note_label)

        self.save_vehicle_button = QPushButton("Save dimensions as custom vehicle…")
        self.save_vehicle_button.clicked.connect(self.save_custom_vehicle)
        form.addRow(self.save_vehicle_button)

        self.edit_geometry_button = QPushButton("Edit axle and coupling geometry…")
        self.edit_geometry_button.clicked.connect(self.edit_vehicle_geometry)
        form.addRow(self.edit_geometry_button)

        self.calculate_action = QAction("Detailed swept path", self)
        self.calculate_action.triggered.connect(lambda: self.calculate_envelope())
        self.calculate_action.setEnabled(False)

        dock.setWidget(panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

    @staticmethod
    def _dimension_spin(maximum: float, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.1, maximum)
        spin.setDecimals(3)
        spin.setSingleStep(0.1)
        spin.setValue(value)
        return spin

    def _build_actions(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self.open_action = QAction("&Open drawing…", self)
        self.open_action.setIcon(action_icon("open"))
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.open_dxf)
        file_menu.addAction(self.open_action)

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

        view_menu = self.menuBar().addMenu("&View")
        fit_action = QAction("Zoom to &Extents", self)
        fit_action.setIcon(action_icon("zoom-extents"))
        fit_action.setShortcut("Z")
        fit_action.triggered.connect(self.view.fit_drawing)
        view_menu.addAction(fit_action)

        toolbar = QToolBar("Path")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(22, 22))
        self.addToolBar(toolbar)
        toolbar.addAction(self.open_action)
        toolbar.addSeparator()

        self.path_combo = QComboBox()
        self.path_combo.setMinimumWidth(180)
        self._refresh_path_picker()
        self.path_combo.currentIndexChanged.connect(self._path_selection_changed)
        toolbar.addWidget(self.path_combo)
        new_path_action = QAction("New path", self)
        new_path_action.triggered.connect(self.new_swept_path)
        toolbar.addAction(new_path_action)
        rename_path_action = QAction("Rename", self)
        rename_path_action.triggered.connect(self.rename_swept_path)
        toolbar.addAction(rename_path_action)
        remove_path_action = QAction("Remove", self)
        remove_path_action.triggered.connect(self.remove_swept_path)
        toolbar.addAction(remove_path_action)
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

        undo_action = QAction("Undo point", self)
        undo_action.setIcon(action_icon("undo"))
        undo_action.setShortcut(QKeySequence("Backspace"))
        undo_action.triggered.connect(self.undo_route_point)
        toolbar.addAction(undo_action)

        clear_action = QAction("Clear path", self)
        clear_action.setIcon(action_icon("clear-path"))
        clear_action.triggered.connect(self.clear_route)
        toolbar.addAction(clear_action)

        toolbar.addAction(self.calculate_action)
        toolbar.addSeparator()
        toolbar.addAction(self.export_action)
        toolbar.addAction(self.save_copy_action)
        self.calculate_action.setIcon(action_icon("calculate"))

    def open_dxf(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Open site drawing",
            "",
            "CAD drawings (*.dxf *.dwg);;DXF drawings (*.dxf);;DWG drawings (*.dwg);;All files (*)",
        )
        if filename:
            self.load_drawing(filename)

    def load_drawing(self, filename: str) -> None:
        self.preview_timer.stop()
        self._preview_generation += 1
        path = Path(filename)
        new_tempdir: tempfile.TemporaryDirectory[str] | None = None
        converted_path: Path | None = None
        try:
            if path.suffix.lower() == ".dwg":
                new_tempdir = tempfile.TemporaryDirectory(prefix="openarchsp-")
                converted_path = Path(new_tempdir.name) / "imported.dxf"
                convert_dwg_to_dxf(path, converted_path)
                document = read_dxf(converted_path)
            else:
                document = read_dxf(path)
            count, drawing_bounds = draw_document(document, self.view.scene)
        except Exception as error:
            if new_tempdir:
                new_tempdir.cleanup()
            QMessageBox.critical(self, "Could not open drawing", str(error))
            return

        if self._dwg_tempdir:
            self._dwg_tempdir.cleanup()
        self._dwg_tempdir = new_tempdir
        self._converted_base_path = converted_path
        self.document = document
        self.drawing_path = path
        self.view.drawing_bounds = drawing_bounds
        margin = max(drawing_bounds.width(), drawing_bounds.height(), 1000.0) * 20
        self.view.scene.setSceneRect(
            drawing_bounds.adjusted(-margin, -margin, margin, margin)
        )
        # draw_document() clears the scene, which also destroys existing overlay items.
        self.route_items.clear()
        self.route_line_item = None
        self.other_path_items.clear()
        self.hover_items.clear()
        self.trace_items.clear()
        self.envelope_items.clear()
        self.envelope_geometry = None
        self.simulation_result = None
        self.swept_paths = [
            SweptPathDocument(
                uuid.uuid4().hex,
                "Swept path 1",
                str(self.vehicle_combo.currentData()),
                "#f28583",
                [],
            )
        ]
        self.active_path_id = self.swept_paths[0].id
        self._refresh_path_picker()
        self._hover_point = None
        self.draw_action.setChecked(False)
        self.set_drawing_mode(False)
        unit_code = int(document.header.get("$INSUNITS", 0) or 0)
        self.units_spin.setValue(self._units_per_metre(unit_code))
        self._render_route()
        self._update_export_state()
        self.draw_action.setEnabled(True)
        self.setWindowTitle(f"OpenArch Swept Paths — {self.drawing_path.name}")
        unit_text = (
            "unitless drawing; assuming metres"
            if unit_code == 0
            else f"drawing unit code {unit_code}"
        )
        import_note = "DWG converted to temporary DXF" if converted_path else "DXF loaded"
        self.status.showMessage(
            f"{self.drawing_path} · {count} entities rendered · {unit_text} · {import_note}"
        )
        self.view.fit_drawing()

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

    def _vehicle_selected(self, _index: int = 0) -> None:
        vehicle = self._selected_vehicle()
        if not self._path_switching:
            self.active_path.vehicle_id = vehicle.id
        has_geometry = vehicle.id in self.vehicle_geometries
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
        if vehicle.source_url:
            source = f'<a href="{vehicle.source_url}">{vehicle.source}</a>'
            self.vehicle_source_label.setText(source)
            self.vehicle_source_label.setOpenExternalLinks(True)
        else:
            self.vehicle_source_label.setText(vehicle.source)

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

    def _vehicle_dimensions_changed(self, _value: float = 0.0) -> None:
        if self._path_switching:
            return
        for path in self.swept_paths:
            path.simulation_result = None
            path.envelope_geometry = None
        self._clear_envelope()
        self._render_other_paths()
        self._update_export_state()
        self._queue_live_preview()

    def _queue_live_preview(self) -> None:
        self._preview_generation += 1
        self._preview_show_errors = False
        self._preview_fast = True
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
                "Right-click or Enter to finish · Esc to stop drawing"
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

    def add_route_point(self, scene_x: float, scene_y: float) -> None:
        self._hover_point = None
        self._render_hover_cursor()
        self.route_points.append((scene_x, scene_y))
        self._clear_envelope()
        self._render_route()
        self._update_export_state()
        self.status.showMessage(f"{self._route_label()}: {len(self.route_points)} points")
        self._queue_live_preview()

    def finish_route(self) -> None:
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

    def undo_route_point(self) -> None:
        if not self.route_points:
            return
        self.route_points.pop()
        self._clear_envelope()
        self._render_route()
        self._update_export_state()
        self.status.showMessage(f"{self._route_label()}: {len(self.route_points)} points")
        self._queue_live_preview()

    def clear_route(self) -> None:
        self.preview_timer.stop()
        self._preview_generation += 1
        self._hover_point = None
        self._render_hover_cursor()
        self.route_points.clear()
        self._clear_envelope()
        self.draw_action.setChecked(False)
        self.set_drawing_mode(False)
        self.preview_timer.stop()
        self._preview_generation += 1
        self._render_route()
        self._update_export_state()
        self.status.showMessage("Reference path cleared")

    def _clear_envelope(self, *, clear_record: bool = True) -> None:
        for item in self.trace_items:
            self.view.scene.removeItem(item)
        self.trace_items.clear()
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
        self._clear_envelope()
        if live:
            self._render_route_line()
        else:
            self._render_route()
        self._queue_live_preview()

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
                color = QColor(path_document.color)
                color.setAlpha(38)
                envelope_item = QGraphicsPathItem(swept_shape)
                envelope_item.setPen(QPen(QColor(path_document.color), 0))
                envelope_item.setBrush(QBrush(color))
                envelope_item.setZValue(15)
                self.view.scene.addItem(envelope_item)
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
        run_geometry = (
            replace(geometry, max_steer_deg=self.steering_limit_spin.value())
            if geometry is not None
            else None
        )
        self._clear_envelope()
        worker = _PreviewWorker(
            token,
            route,
            run_geometry,
            fast_preview=fast_preview,
            scale=scale,
            corner_radius_m=self.corner_radius_spin.value(),
            spacing_m=2.0 if fast_preview else 0.25,
            speed_kmh=self.speed_spin.value(),
            lock_to_lock_s=self.lock_to_lock_spin.value(),
            rigid_length=self.length_spin.value(),
            rigid_width=self.width_spin.value(),
        )
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
            self._clear_envelope()
            self._preview_validity = "invalid"
            self._render_hover_cursor()
            self.status.showMessage(f"Candidate path is unreachable · {error}")
            if self._preview_show_errors:
                QMessageBox.warning(self, "Could not calculate preview", error)
        else:
            result, envelope = payload
            self.envelope_geometry = envelope
            self.active_path.simulation_result = result
            self.active_path.envelope_geometry = envelope
            self.active_path.simulation_scale = self.units_spin.value()
            self._render_envelope(envelope)
            if result is None:
                self.simulation_result = None
                vehicle = self._selected_vehicle()
                self.status.showMessage(
                    f"Rigid-body footprint preview · {vehicle.name} · "
                    f"{self.length_spin.value():.2f} m × {self.width_spin.value():.2f} m"
                )
                self._preview_validity = "valid"
            else:
                self.simulation_result = result
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
                    f"max wheel steer {max_wheel_steer:.1f}° (centre {max_steer:.1f}°) · "
                    f"axle off-tracking {max_offtracking:.2f} m · "
                    f"articulation {result.maximum_articulation_deg:.1f}° · "
                    f"steering rate {result.max_steering_rate_deg_s:.1f}°/s · "
                    f"target path error {result.maximum_path_error_m:.2f} m{state}"
                )
            self._render_hover_cursor()

        if token != self._preview_generation and len(self._current_preview_route()) >= 2:
            self.preview_timer.start(0)
            self._update_export_state()

    def _render_simulation_traces(
        self, result: SimulationResult, geometry: CombinationGeometry, scale: float
    ) -> None:
        colors = ("#e3e7eb", "#82c4b3", "#d7aa72", "#9fb6e2")

        def add_trace(
            points: Sequence[tuple[float, float]],
            color: str,
            width: float,
            *,
            dashed: bool = False,
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
                add_trace(wheel_path, "#84a69d", 0.8)

    def _render_current_vehicle(
        self, result: SimulationResult, geometry: CombinationGeometry, scale: float
    ) -> None:
        colors = ("#3d8d78", "#b17c43", "#5b7fa8", "#9a6e9b")
        final_poses: list[tuple[tuple[float, float], float]] = []
        for unit_index, (trajectory, unit_geometry) in enumerate(
            zip(result.units, geometry.units)
        ):
            reference = trajectory.reference_points[-1]
            heading = trajectory.headings_rad[-1]
            final_poses.append((reference, heading))
            forward = (math.cos(heading), math.sin(heading))
            normal = (-math.sin(heading), math.cos(heading))

            def world(longitudinal: float, lateral: float) -> tuple[float, float]:
                return (
                    reference[0] + forward[0] * longitudinal + normal[0] * lateral,
                    reference[1] + forward[1] * longitudinal + normal[1] * lateral,
                )

            outline = QPainterPath()
            corners = [
                world(unit_geometry.front_extent_m, unit_geometry.width_m / 2),
                world(unit_geometry.front_extent_m, -unit_geometry.width_m / 2),
                world(-unit_geometry.rear_extent_m, -unit_geometry.width_m / 2),
                world(-unit_geometry.rear_extent_m, unit_geometry.width_m / 2),
            ]
            outline.moveTo(corners[0][0] * scale, -corners[0][1] * scale)
            for x, y in corners[1:]:
                outline.lineTo(x * scale, -y * scale)
            outline.closeSubpath()
            body = QGraphicsPathItem(outline)
            body_color = QColor(colors[unit_index % len(colors)])
            body_color.setAlpha(62)
            body.setBrush(QBrush(body_color))
            pen = QPen(QColor(colors[unit_index % len(colors)]))
            pen.setCosmetic(True)
            pen.setWidthF(2.0)
            body.setPen(pen)
            body.setZValue(62)
            body.setToolTip(unit_geometry.name)
            self.view.scene.addItem(body)
            self.trace_items.append(body)

            steer_angles = (0.0, 0.0)
            if unit_index == 0 and result.front_wheel_angles_deg:
                steer_angles = result.front_wheel_angles_deg[-1]
            for axle_offset in unit_geometry.axle_offsets_m:
                is_steer_axle = (
                    unit_index == 0
                    and math.isclose(axle_offset, geometry.steer_axle_m, abs_tol=1e-6)
                )
                for side, steer_angle in zip((1, -1), steer_angles):
                    wheel_heading = heading + (math.radians(steer_angle) if is_steer_axle else 0.0)
                    wheel_forward = (math.cos(wheel_heading), math.sin(wheel_heading))
                    wheel_center = world(axle_offset, side * unit_geometry.track_m / 2)
                    half_length = 0.48
                    half_width = 0.12
                    wheel_normal = (-wheel_forward[1], wheel_forward[0])
                    wheel_corners = [
                        (
                            wheel_center[0] + wheel_forward[0] * longitudinal + wheel_normal[0] * lateral,
                            wheel_center[1] + wheel_forward[1] * longitudinal + wheel_normal[1] * lateral,
                        )
                        for longitudinal, lateral in (
                            (half_length, half_width),
                            (half_length, -half_width),
                            (-half_length, -half_width),
                            (-half_length, half_width),
                        )
                    ]
                    wheel_path = QPainterPath()
                    wheel_path.moveTo(wheel_corners[0][0] * scale, -wheel_corners[0][1] * scale)
                    for x, y in wheel_corners[1:]:
                        wheel_path.lineTo(x * scale, -y * scale)
                    wheel_path.closeSubpath()
                    wheel = QGraphicsPathItem(wheel_path)
                    wheel.setBrush(QBrush(QColor("#252a28")))
                    wheel.setPen(QPen(QColor("#f7f5ef"), 0))
                    wheel.setZValue(64)
                    wheel_label = f"{unit_geometry.name} wheel"
                    if is_steer_axle:
                        wheel_label += f" · {steer_angle:.1f}° steer"
                    wheel.setToolTip(wheel_label)
                    self.view.scene.addItem(wheel)
                    self.trace_items.append(wheel)

        # Draw each live coupling between the actual tractor and trailer poses.
        for index in range(1, len(final_poses)):
            towing_reference, towing_heading = final_poses[index - 1]
            trailer_reference, trailer_heading = final_poses[index]
            towing_geometry = geometry.units[index - 1]
            trailer_geometry = geometry.units[index]
            towing_point = (
                towing_reference[0] - math.cos(towing_heading) * towing_geometry.rear_hitch_m,
                towing_reference[1] - math.sin(towing_heading) * towing_geometry.rear_hitch_m,
            )
            trailer_point = (
                trailer_reference[0] + math.cos(trailer_heading) * trailer_geometry.front_hitch_m,
                trailer_reference[1] + math.sin(trailer_heading) * trailer_geometry.front_hitch_m,
            )
            coupling = QPainterPath()
            coupling.moveTo(towing_point[0] * scale, -towing_point[1] * scale)
            coupling.lineTo(trailer_point[0] * scale, -trailer_point[1] * scale)
            pen = QPen(QColor("#d97732"))
            pen.setCosmetic(True)
            pen.setWidthF(2.0)
            pen.setStyle(Qt.PenStyle.DashLine)
            coupling_item = QGraphicsPathItem(coupling)
            coupling_item.setPen(pen)
            coupling_item.setZValue(63)
            self.view.scene.addItem(coupling_item)
            self.trace_items.append(coupling_item)

        self._render_vehicle_start(result, geometry, scale)

    def _render_vehicle_start(
        self, result: SimulationResult, geometry: CombinationGeometry, scale: float
    ) -> None:
        colors = ("#3d8d78", "#b17c43", "#5b7fa8", "#9a6e9b")
        for unit_index, (trajectory, unit_geometry) in enumerate(
            zip(result.units, geometry.units)
        ):
            reference = trajectory.reference_points[0]
            heading = trajectory.headings_rad[0]
            forward = (math.cos(heading), math.sin(heading))
            normal = (-math.sin(heading), math.cos(heading))

            def world(longitudinal: float, lateral: float) -> tuple[float, float]:
                return (
                    reference[0] + forward[0] * longitudinal + normal[0] * lateral,
                    reference[1] + forward[1] * longitudinal + normal[1] * lateral,
                )

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
            item = QGraphicsPathItem(shape)
            color = QColor(colors[unit_index % len(colors)])
            color.setAlpha(95)
            item.setBrush(QBrush(color))
            pen = QPen(QColor(colors[unit_index % len(colors)]))
            pen.setCosmetic(True)
            pen.setWidthF(2.0)
            item.setPen(pen)
            item.setZValue(61)
            self.view.scene.addItem(item)
            self.trace_items.append(item)

            steer_angles = result.front_wheel_angles_deg[0] if unit_index == 0 else (0.0, 0.0)
            for axle_offset in unit_geometry.axle_offsets_m:
                is_steer = (
                    unit_index == 0
                    and math.isclose(axle_offset, geometry.steer_axle_m, abs_tol=1e-6)
                )
                for side, angle in zip((1, -1), steer_angles):
                    wheel_heading = heading + (math.radians(angle) if is_steer else 0.0)
                    wheel_forward = (math.cos(wheel_heading), math.sin(wheel_heading))
                    wheel_normal = (-wheel_forward[1], wheel_forward[0])
                    center = world(axle_offset, side * unit_geometry.track_m / 2)
                    wheel_corners = [
                        (
                            center[0] + wheel_forward[0] * along + wheel_normal[0] * across,
                            center[1] + wheel_forward[1] * along + wheel_normal[1] * across,
                        )
                        for along, across in ((0.48, 0.12), (0.48, -0.12), (-0.48, -0.12), (-0.48, 0.12))
                    ]
                    wheel_shape = QPainterPath()
                    wheel_shape.moveTo(wheel_corners[0][0] * scale, -wheel_corners[0][1] * scale)
                    for x, y in wheel_corners[1:]:
                        wheel_shape.lineTo(x * scale, -y * scale)
                    wheel_shape.closeSubpath()
                    wheel = QGraphicsPathItem(wheel_shape)
                    wheel.setBrush(QBrush(QColor("#181a1d")))
                    wheel.setPen(QPen(QColor("#f7f5ef"), 0))
                    wheel.setZValue(63)
                    self.view.scene.addItem(wheel)
                    self.trace_items.append(wheel)

        lead = geometry.units[0]
        start_reference = result.units[0].reference_points[0]
        start_heading = result.units[0].headings_rad[0]
        center_offset = (lead.front_extent_m - lead.rear_extent_m) / 2
        label_x = (start_reference[0] + math.cos(start_heading) * center_offset) * scale
        label_y = -(start_reference[1] + math.sin(start_heading) * center_offset) * scale
        vehicle_name = self._selected_vehicle().name
        label = QGraphicsSimpleTextItem(vehicle_name)
        label.setBrush(QColor("#fff4cf"))
        label.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        label_bounds = label.boundingRect()
        label.setPos(label_x - label_bounds.width() / 2, label_y - label_bounds.height() / 2)
        label.setZValue(65)
        label.setToolTip(f"{self.active_path.name} · {vehicle_name}")
        self.view.scene.addItem(label)
        self.trace_items.append(label)

    def _render_envelope(self, geometry: object) -> None:
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        for polygon in polygons(geometry):
            self._add_ring(path, polygon.exterior.coords)
            for interior in polygon.interiors:
                self._add_ring(path, interior.coords)

        envelope = QGraphicsPathItem(path)
        envelope.setPen(QPen(QColor("#84a69d"), 0))
        envelope.setBrush(QBrush(QColor(132, 166, 157, 92)))
        envelope.setZValue(50)
        self.view.scene.addItem(envelope)
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
            output = read_dxf(self._converted_base_path or self.drawing_path)
        else:
            output = ezdxf.new("R2010")
            if self.document is not None:
                output.header["$INSUNITS"] = self.document.header.get("$INSUNITS", 0)

        modelspace = output.modelspace()
        for path_index, path_document in enumerate(self.swept_paths, start=1):
            if len(path_document.points) >= 2:
                self._write_path_dxf(modelspace, output, path_document, path_index)
        output.saveas(target)

    def _write_path_dxf(
        self, modelspace: object, output: object, path_document: SweptPathDocument, index: int
    ) -> None:
        scale = path_document.simulation_scale
        prefix = f"OASP_P{index}"
        path_layer = f"{prefix}_STEER"
        node_layer = f"{prefix}_NODES"
        outside_layer = f"{prefix}_OUTSIDE"
        block_layer = f"{prefix}_VEHICLE"
        colors = (1, 4, 30, 6, 3, 5)
        layer_color = colors[(index - 1) % len(colors)]

        def ensure_layer(name: str, color: int, lineweight: int | None = None) -> None:
            if name not in output.layers:
                attrs: dict[str, object] = {"color": color}
                if lineweight is not None:
                    attrs["lineweight"] = lineweight
                output.layers.new(name, dxfattribs=attrs)

        ensure_layer(path_layer, layer_color, 35)
        ensure_layer(node_layer, layer_color)
        ensure_layer(outside_layer, layer_color, 25)
        ensure_layer(block_layer, 7)
        result = path_document.simulation_result
        steer_points = (
            [(x * scale, y * scale) for x, y in result.target_steer_axle_path]
            if result is not None
            else path_document.points
        )
        modelspace.add_lwpolyline(steer_points, dxfattribs={"layer": path_layer})
        if result is not None:
            actual_layer = f"{prefix}_ACTUAL"
            ensure_layer(actual_layer, 7)
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

        block_name = f"OASP_VEHICLE_P{index}_{path_document.id[:8].upper()}"
        vehicle_block = output.blocks.new(block_name)
        lead_reference = result.units[0].reference_points[0]
        lead_heading = result.units[0].headings_rad[0]

        def block_point(point: tuple[float, float]) -> tuple[float, float]:
            dx = point[0] - lead_reference[0]
            dy = point[1] - lead_reference[1]
            return (
                (math.cos(lead_heading) * dx + math.sin(lead_heading) * dy) * scale,
                (-math.sin(lead_heading) * dx + math.cos(lead_heading) * dy) * scale,
            )

        for unit_index, (trajectory, unit_geometry) in enumerate(
            zip(result.units, geometry.units), start=1
        ):
            axle_layer = f"{prefix}_AXLE_U{unit_index}"
            wheel_layer = f"{prefix}_WHEEL_U{unit_index}"
            edge_layer = f"{prefix}_EDGE_U{unit_index}"
            ensure_layer(axle_layer, colors[(unit_index + 1) % len(colors)])
            ensure_layer(wheel_layer, 94)
            ensure_layer(edge_layer, colors[(unit_index + 1) % len(colors)])
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

            # Place an oriented vehicle block at the first waypoint, including its wheels.
            reference = trajectory.reference_points[0]
            heading = trajectory.headings_rad[0]

            def world(longitudinal: float, lateral: float) -> tuple[float, float]:
                return (
                    reference[0] + math.cos(heading) * longitudinal - math.sin(heading) * lateral,
                    reference[1] + math.sin(heading) * longitudinal + math.cos(heading) * lateral,
                )

            corners = [
                world(unit_geometry.front_extent_m, unit_geometry.width_m / 2),
                world(unit_geometry.front_extent_m, -unit_geometry.width_m / 2),
                world(-unit_geometry.rear_extent_m, -unit_geometry.width_m / 2),
                world(-unit_geometry.rear_extent_m, unit_geometry.width_m / 2),
            ]
            vehicle_block.add_lwpolyline(
                [block_point(point) for point in corners],
                close=True,
                dxfattribs={"layer": "0"},
            )
            steer_angles = result.front_wheel_angles_deg[0] if unit_index == 1 else (0.0, 0.0)
            for axle_offset in unit_geometry.axle_offsets_m:
                for side, angle in zip((1, -1), steer_angles):
                    is_steer = unit_index == 1 and math.isclose(
                        axle_offset, geometry.steer_axle_m, abs_tol=1e-6
                    )
                    wheel_heading = heading + (math.radians(angle) if is_steer else 0.0)
                    center = world(axle_offset, side * unit_geometry.track_m / 2)
                    wf = (math.cos(wheel_heading), math.sin(wheel_heading))
                    wn = (-wf[1], wf[0])
                    wheel_corners = [
                        (
                            center[0] + wf[0] * along + wn[0] * across,
                            center[1] + wf[1] * along + wn[1] * across,
                        )
                        for along, across in ((0.48, 0.12), (0.48, -0.12), (-0.48, -0.12), (-0.48, 0.12))
                    ]
                    vehicle_block.add_lwpolyline(
                        [block_point(point) for point in wheel_corners],
                        close=True,
                        dxfattribs={"layer": "0"},
                    )
            if unit_index == 1:
                center = world(
                    (unit_geometry.front_extent_m - unit_geometry.rear_extent_m) / 2,
                    0,
                )
                vehicle_block.add_text(
                    vehicle.name,
                    dxfattribs={
                        "height": max(1.0 * scale, 0.2),
                        "layer": "0",
                    },
                ).set_placement(block_point(center))

        modelspace.add_blockref(
            block_name,
            (lead_reference[0] * scale, lead_reference[1] * scale, 0),
            dxfattribs={
                "layer": block_layer,
                "rotation": math.degrees(lead_heading),
            },
        )

        if len(result.units) > 1:
            coupling_layer = f"{prefix}_COUPLING"
            ensure_layer(coupling_layer, 30)
            for index in range(1, len(result.units)):
                towing = result.units[index - 1]
                trailer = result.units[index]
                towing_geometry = geometry.units[index - 1]
                trailer_geometry = geometry.units[index]
                towing_ref = towing.reference_points[0]
                towing_heading = towing.headings_rad[0]
                trailer_ref = trailer.reference_points[0]
                trailer_heading = trailer.headings_rad[0]
                coupling_points = (
                    (
                        towing_ref[0] - math.cos(towing_heading) * towing_geometry.rear_hitch_m,
                        towing_ref[1] - math.sin(towing_heading) * towing_geometry.rear_hitch_m,
                    ),
                    (
                        trailer_ref[0] + math.cos(trailer_heading) * trailer_geometry.front_hitch_m,
                        trailer_ref[1] + math.sin(trailer_heading) * trailer_geometry.front_hitch_m,
                    ),
                )
                modelspace.add_lwpolyline(
                    [(x * scale, y * scale) for x, y in coupling_points],
                    dxfattribs={"layer": coupling_layer},
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

    def closeEvent(self, event: object) -> None:
        if self._dwg_tempdir:
            self._dwg_tempdir.cleanup()
            self._dwg_tempdir = None
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("OpenArch Swept Paths")
    app.setWindowIcon(QIcon(str(asset_path("app-icon.svg"))))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()






