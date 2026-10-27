"""Application entry point for OpenArch Swept Paths."""

from __future__ import annotations

import json
import sys
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import ezdxf
from shapely.affinity import scale as scale_geometry
from PySide6.QtCore import QSettings, QSize, QTimer, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
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
    QGraphicsPathItem,
    QGraphicsScene,
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
    finishRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.drawing_mode = False
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setBackgroundBrush(Qt.GlobalColor.white)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

    def fit_drawing(self) -> None:
        if self.scene.items():
            self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event: object) -> None:
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.scale(factor, factor)

    def mousePressEvent(self, event: object) -> None:
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

    def keyPressEvent(self, event: object) -> None:
        if event.key() == Qt.Key.Key_Escape and self.drawing_mode:
            self.finishRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


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
            "QMainWindow { background: #f0efeb; }"
            "QToolBar { background: #f0efeb; border: 0; border-bottom: 1px solid #d7d4ce; "
            "spacing: 8px; padding: 6px; }"
            "QToolButton { color: #1a1a1a; padding: 5px 8px; border-radius: 5px; }"
            "QToolButton:hover { background: #e5e4df; }"
            "QToolButton:checked { background: #84a69d; }"
            "QDockWidget::title { background: #84a69d; color: #1a1a1a; "
            "padding: 7px; font-weight: 600; }"
            "QComboBox, QDoubleSpinBox, QPushButton { padding: 5px; }"
            "QPushButton { background: #f28583; color: #1a1a1a; border: 0; "
            "border-radius: 4px; font-weight: 600; }"
            "QPushButton:hover { background: #ed7775; }"
        )
        self.document = None
        self.drawing_path: Path | None = None
        self._dwg_tempdir: tempfile.TemporaryDirectory[str] | None = None
        self._converted_base_path: Path | None = None
        self.route_points: list[tuple[float, float]] = []
        self.route_items: list[object] = []
        self.trace_items: list[object] = []
        self.envelope_items: list[object] = []
        self.envelope_geometry = None
        self.simulation_result: SimulationResult | None = None
        self.simulation_scale = 1.0
        self.vehicle_geometries = self._load_vehicle_geometries()
        self._last_vehicle_geometry_mode: bool | None = None

        self.vehicle_definitions = self._load_vehicle_definitions()
        self.view = DrawingView()
        self.setCentralWidget(self.view)
        self.view.worldClicked.connect(self.add_route_point)
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
        self.preview_timer.setInterval(180)
        self.preview_timer.timeout.connect(self._preview_current_route)

        self.status = QStatusBar()
        self.status.showMessage("Open a DXF or DWG site drawing to begin")
        self.setStatusBar(self.status)
        self._vehicle_selected()

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
        self.vehicle_note_label.setStyleSheet("color: #6b5a3e;")
        form.addRow(self.vehicle_note_label)

        self.save_vehicle_button = QPushButton("Save dimensions as custom vehicle…")
        self.save_vehicle_button.clicked.connect(self.save_custom_vehicle)
        form.addRow(self.save_vehicle_button)

        self.edit_geometry_button = QPushButton("Edit axle and coupling geometry…")
        self.edit_geometry_button.clicked.connect(self.edit_vehicle_geometry)
        form.addRow(self.edit_geometry_button)

        self.calculate_action = QAction("Preview swept path", self)
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

        self.export_action = QAction("Export path and envelope as DXF…", self)
        self.export_action.setIcon(action_icon("export"))
        self.export_action.triggered.connect(self.export_path)
        self.export_action.setEnabled(False)
        file_menu.addAction(self.export_action)

        self.save_copy_action = QAction("Save drawing copy with path and envelope as…", self)
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
            count, _ = draw_document(document, self.view.scene)
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
        # draw_document() clears the scene, which also destroys existing overlay items.
        self.route_items.clear()
        self.trace_items.clear()
        self.envelope_items.clear()
        self.envelope_geometry = None
        self.simulation_result = None
        self.route_points.clear()
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
        has_geometry = vehicle.id in self.vehicle_geometries
        if (
            self._last_vehicle_geometry_mode is not None
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
        self._clear_envelope()
        self._update_export_state()
        self._queue_live_preview()

    def _vehicle_dimensions_changed(self, _value: float = 0.0) -> None:
        self._clear_envelope()
        self._update_export_state()
        self._queue_live_preview()

    def _queue_live_preview(self) -> None:
        if len(self.route_points) >= 2 and self.document is not None:
            self.preview_timer.start()

    def _preview_current_route(self) -> None:
        if len(self.route_points) >= 2:
            self.calculate_envelope(show_errors=False)

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
        settings = QSettings("OpenArch", "OpenArchSweptPaths")
        settings.setValue(
            "vehicleGeometries",
            json.dumps(
                {key: value.to_dict() for key, value in self.vehicle_geometries.items()}
            ),
        )
        self._clear_envelope()
        self._vehicle_selected()
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
                f"Click {path_name} points · swept preview updates after each click · "
                "Right-click or Enter to finish · Esc to stop drawing"
            )
        else:
            self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.status.showMessage("Path edit mode off · Wheel zooms · Drag pans")

    def add_route_point(self, scene_x: float, scene_y: float) -> None:
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
        self.route_points.clear()
        self._clear_envelope()
        self.draw_action.setChecked(False)
        self.set_drawing_mode(False)
        self._render_route()
        self._update_export_state()
        self.status.showMessage("Reference path cleared")

    def _clear_envelope(self) -> None:
        for item in self.trace_items:
            self.view.scene.removeItem(item)
        self.trace_items.clear()
        for item in self.envelope_items:
            self.view.scene.removeItem(item)
        self.envelope_items.clear()
        self.envelope_geometry = None
        self.simulation_result = None

    def _render_route(self) -> None:
        for item in self.route_items:
            self.view.scene.removeItem(item)
        self.route_items.clear()
        if not self.route_points:
            return

        path = QPainterPath()
        first_x, first_y = self.route_points[0]
        path.moveTo(first_x, -first_y)
        for x, y in self.route_points[1:]:
            path.lineTo(x, -y)
        pen = QPen(QColor("#f28583"))
        pen.setCosmetic(True)
        pen.setWidthF(2.5)
        line_item = QGraphicsPathItem(path)
        line_item.setPen(pen)
        line_item.setZValue(100)
        self.view.scene.addItem(line_item)
        self.route_items.append(line_item)

        marker_pen = QPen(Qt.GlobalColor.white)
        marker_pen.setCosmetic(True)
        marker_pen.setWidthF(1.5)
        for index, (x, y) in enumerate(self.route_points):
            marker = QGraphicsEllipseItem(x - 0.12, -y - 0.12, 0.24, 0.24)
            marker.setBrush(QColor("#f28583"))
            marker.setPen(marker_pen)
            marker.setZValue(101)
            marker.setToolTip(f"Point {index + 1}: {x:.3f}, {y:.3f}")
            self.view.scene.addItem(marker)
            self.route_items.append(marker)

    def calculate_envelope(
        self, _checked: bool = False, *, show_errors: bool = True
    ) -> None:
        if len(self.route_points) < 2:
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

        scale = self.units_spin.value()
        self._clear_envelope()
        try:
            if geometry is not None:
                run_geometry = replace(
                    geometry, max_steer_deg=self.steering_limit_spin.value()
                )
                route_metres = [
                    (x / scale, y / scale) for x, y in self.route_points
                ]
                result = simulate_combination(
                    route_metres,
                    run_geometry,
                    corner_radius_m=self.corner_radius_spin.value(),
                    spacing_m=0.25,
                    speed_kmh=self.speed_spin.value(),
                    lock_to_lock_s=self.lock_to_lock_spin.value(),
                )
                self.simulation_result = result
                self.simulation_scale = scale
                self.envelope_geometry = scale_geometry(
                    result.envelope, xfact=scale, yfact=scale, origin=(0, 0)
                )
                self._render_simulation_traces(result, scale)
                self._render_envelope(self.envelope_geometry)
                max_steer = max(abs(value) for value in result.steering_angles_deg)
                max_wheel_steer = max(
                    (abs(angle) for pair in result.front_wheel_angles_deg for angle in pair),
                    default=0.0,
                )
                max_offtracking = max(result.offtracking_by_axle_m.values(), default=0.0)
                simulation_note = (
                    " · steering actuator saturated"
                    if result.steering_rate_saturated
                    else ""
                )
                if not result.path_complete:
                    simulation_note += " · target path not completed"
                self.status.showMessage(
                    f"Swept path preview · {geometry.name} · "
                    f"max wheel steer {max_wheel_steer:.1f}° (centre {max_steer:.1f}°) · "
                    f"axle off-tracking {max_offtracking:.2f} m · "
                    f"articulation {result.maximum_articulation_deg:.1f}° · "
                    f"steering rate {result.max_steering_rate_deg_s:.1f}°/s · "
                    f"target path error {result.maximum_path_error_m:.2f} m{simulation_note}"
                )
                return

            result = swept_footprint(
                self.route_points,
                self.length_spin.value() * scale,
                self.width_spin.value() * scale,
                max(0.25 * scale, 1e-6),
            )
        except Exception as error:
            if show_errors:
                QMessageBox.warning(self, "Could not calculate preview", str(error))
            else:
                self.status.showMessage(f"Live preview unavailable · {error}")
            return

        self.envelope_geometry = result
        self._render_envelope(result)
        self.status.showMessage(
            f"Rigid-body footprint preview · {vehicle.name} · "
            f"{self.length_spin.value():.2f} m × {self.width_spin.value():.2f} m"
        )

    def _render_simulation_traces(self, result: SimulationResult, scale: float) -> None:
        colors = ("#1a1a1a", "#627f77", "#8b7560", "#6c7f94")

        def add_trace(
            points: Sequence[tuple[float, float]],
            color: str,
            width: float,
            *,
            dashed: bool = False,
        ) -> None:
            if len(points) < 2:
                return
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

        # Show the target path in coral and the rate-limited actual path in charcoal.
        add_trace(result.target_steer_axle_path, "#f28583", 2.0, dashed=True)
        add_trace(result.steer_axle_path, "#1a1a1a", 2.4)
        for unit_index, unit in enumerate(result.units):
            color = colors[unit_index % len(colors)]
            for axle_path in unit.axle_traces.values():
                add_trace(axle_path, color, 1.3)
            for wheel_path in unit.wheel_traces.values():
                add_trace(wheel_path, "#84a69d", 0.8)

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
        has_path = self.document is not None and len(self.route_points) >= 2
        self.export_action.setEnabled(has_path)
        self.save_copy_action.setEnabled(has_path)
        vehicle = self._selected_vehicle()
        can_calculate = has_path and (
            vehicle.supports_preview or vehicle.id in self.vehicle_geometries
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
        if "OASP_STEERING_PATH" not in output.layers:
            output.layers.new(
                "OASP_STEERING_PATH",
                dxfattribs={"color": 1, "lineweight": 35},
            )
        steer_path = (
            [
                (x * self.simulation_scale, y * self.simulation_scale)
                for x, y in self.simulation_result.target_steer_axle_path
            ]
            if self.simulation_result is not None
            else self.route_points
        )
        modelspace.add_lwpolyline(
            steer_path,
            dxfattribs={"layer": "OASP_STEERING_PATH"},
        )

        if self.simulation_result is not None:
            actual_steer_layer = "OASP_ACTUAL_STEER_PATH"
            if actual_steer_layer not in output.layers:
                output.layers.new(actual_steer_layer, dxfattribs={"color": 7})
            modelspace.add_lwpolyline(
                [
                    (x * self.simulation_scale, y * self.simulation_scale)
                    for x, y in self.simulation_result.steer_axle_path
                ],
                dxfattribs={"layer": actual_steer_layer},
            )
            axle_colors = (4, 5, 6, 3)
            for unit_index, unit in enumerate(self.simulation_result.units, start=1):
                axle_layer = f"OASP_AXLE_U{unit_index}"
                wheel_layer = f"OASP_WHEEL_U{unit_index}"
                if axle_layer not in output.layers:
                    output.layers.new(
                        axle_layer,
                        dxfattribs={"color": axle_colors[(unit_index - 1) % len(axle_colors)]},
                    )
                if wheel_layer not in output.layers:
                    output.layers.new(wheel_layer, dxfattribs={"color": 94})
                for points in unit.axle_traces.values():
                    modelspace.add_lwpolyline(
                        [
                            (x * self.simulation_scale, y * self.simulation_scale)
                            for x, y in points
                        ],
                        dxfattribs={"layer": axle_layer},
                    )
                for points in unit.wheel_traces.values():
                    modelspace.add_lwpolyline(
                        [
                            (x * self.simulation_scale, y * self.simulation_scale)
                            for x, y in points
                        ],
                        dxfattribs={"layer": wheel_layer},
                    )

        if self.envelope_geometry is not None:
            if "OASP_SWEPT_ENVELOPE_PREVIEW" not in output.layers:
                output.layers.new(
                    "OASP_SWEPT_ENVELOPE_PREVIEW",
                    dxfattribs={"color": 30, "lineweight": 25},
                )
            for polygon in polygons(self.envelope_geometry):
                for coordinates in (
                    polygon.exterior.coords,
                    *(interior.coords for interior in polygon.interiors),
                ):
                    vertices = [(float(x), float(y)) for x, y in list(coordinates)[:-1]]
                    if len(vertices) >= 3:
                        modelspace.add_lwpolyline(
                            vertices,
                            close=True,
                            dxfattribs={"layer": "OASP_SWEPT_ENVELOPE_PREVIEW"},
                        )
        output.saveas(target)

    def export_path(self) -> None:
        if len(self.route_points) < 2:
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "Export path and envelope", "swept_path.dxf", "DXF drawings (*.dxf)"
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
        result_type = "path and envelope preview" if self.envelope_geometry else "path"
        self.status.showMessage(f"Exported {result_type} to {target}")

    def save_drawing_copy(self) -> None:
        if len(self.route_points) < 2:
            return
        default_name = (
            f"{self.drawing_path.stem}_with_swept_path.dxf"
            if self.drawing_path
            else "site_with_swept_path.dxf"
        )
        target, _ = QFileDialog.getSaveFileName(
            self,
            "Save drawing copy with path and envelope",
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
        result_type = "path and envelope preview" if self.envelope_geometry else "path"
        self.status.showMessage(f"Saved drawing copy with {result_type} to {target}")

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






