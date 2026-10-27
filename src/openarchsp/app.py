"""Application entry point for OpenArch Swept Paths."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import ezdxf
from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGraphicsEllipseItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QToolBar,
    QWidget,
)

from openarchsp.drawing import draw_document, read_dxf
from openarchsp.swept_path import polygons, swept_footprint
from openarchsp.vehicles import (
    CUSTOM_RIGID_VEHICLE,
    STANDARD_VEHICLES,
    VehicleDefinition,
)


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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("OpenArch Swept Paths")
        self.resize(1280, 820)
        self.document = None
        self.drawing_path: Path | None = None
        self.route_points: list[tuple[float, float]] = []
        self.route_items: list[object] = []
        self.envelope_items: list[object] = []
        self.envelope_geometry = None

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

        self.status = QStatusBar()
        self.status.showMessage("Open a DXF site drawing to begin")
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

        self.calculate_action = QAction("Calculate rigid preview", self)
        self.calculate_action.triggered.connect(self.calculate_envelope)
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
        self.open_action = QAction("&Open DXF…", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.open_dxf)
        file_menu.addAction(self.open_action)

        self.export_action = QAction("Export path and envelope as DXF…", self)
        self.export_action.triggered.connect(self.export_path)
        self.export_action.setEnabled(False)
        file_menu.addAction(self.export_action)

        self.save_copy_action = QAction("Save drawing copy with path and envelope as…", self)
        self.save_copy_action.triggered.connect(self.save_drawing_copy)
        self.save_copy_action.setEnabled(False)
        file_menu.addAction(self.save_copy_action)

        view_menu = self.menuBar().addMenu("&View")
        fit_action = QAction("Zoom to &Extents", self)
        fit_action.setShortcut("Z")
        fit_action.triggered.connect(self.view.fit_drawing)
        view_menu.addAction(fit_action)

        toolbar = QToolBar("Path")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.draw_action = QAction("Draw reference path", self)
        self.draw_action.setCheckable(True)
        self.draw_action.setEnabled(False)
        self.draw_action.triggered.connect(self.set_drawing_mode)
        toolbar.addAction(self.draw_action)

        self.finish_action = QAction("Finish path", self)
        self.finish_action.setShortcut(QKeySequence("Return"))
        self.finish_action.setEnabled(False)
        self.finish_action.triggered.connect(self.finish_route)
        toolbar.addAction(self.finish_action)

        undo_action = QAction("Undo point", self)
        undo_action.setShortcut(QKeySequence("Backspace"))
        undo_action.triggered.connect(self.undo_route_point)
        toolbar.addAction(undo_action)

        clear_action = QAction("Clear path", self)
        clear_action.triggered.connect(self.clear_route)
        toolbar.addAction(clear_action)

        toolbar.addAction(self.calculate_action)

    def open_dxf(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Open site drawing",
            "",
            "DXF drawings (*.dxf);;All files (*)",
        )
        if filename:
            self.load_dxf(filename)

    def load_dxf(self, filename: str) -> None:
        try:
            document = read_dxf(filename)
            count, _ = draw_document(document, self.view.scene)
        except Exception as error:
            QMessageBox.critical(self, "Could not open DXF", str(error))
            return

        self.document = document
        self.drawing_path = Path(filename)
        # draw_document() clears the scene, which also destroys existing overlay items.
        self.route_items.clear()
        self.envelope_items.clear()
        self.envelope_geometry = None
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
            "unitless DXF; assuming metres"
            if unit_code == 0
            else f"DXF unit code {unit_code}"
        )
        self.status.showMessage(
            f"{self.drawing_path} · {count} entities rendered · {unit_text}"
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
        self.length_spin.setValue(vehicle.length_m)
        self.width_spin.setValue(vehicle.width_m)
        if vehicle.source_url:
            source = f'<a href="{vehicle.source_url}">{vehicle.source}</a>'
            self.vehicle_source_label.setText(source)
            self.vehicle_source_label.setOpenExternalLinks(True)
        else:
            self.vehicle_source_label.setText(vehicle.source)

        self.save_vehicle_button.setEnabled(vehicle.supports_preview)
        if vehicle.supports_preview:
            self.vehicle_note_label.setText(
                "Preview model: one rigid rectangle follows the drawn body-centre path. "
                "It does not calculate steering geometry or axle off-tracking. "
                "Confirm nominal widths against your project vehicle source."
            )
        else:
            self.vehicle_note_label.setText(
                "This combination is in the catalogue, but its tractor, trailer, "
                "axle and coupling geometry is not configured yet. Calculation is disabled. "
                + vehicle.notes
            )
        self._clear_envelope()
        self._update_export_state()

    def _vehicle_dimensions_changed(self, _value: float = 0.0) -> None:
        self._clear_envelope()
        self._update_export_state()

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
            self.status.showMessage(
                "Click body-centre path points · Right-click or Enter to finish · Esc to stop drawing"
            )
        else:
            self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.status.showMessage("Path edit mode off · Wheel zooms · Drag pans")

    def add_route_point(self, scene_x: float, scene_y: float) -> None:
        self.route_points.append((scene_x, scene_y))
        self._clear_envelope()
        self._render_route()
        self._update_export_state()
        self.status.showMessage(f"Reference path: {len(self.route_points)} points")

    def finish_route(self) -> None:
        self.draw_action.setChecked(False)
        self.set_drawing_mode(False)
        if len(self.route_points) < 2:
            self.status.showMessage("Add at least two points to define a path")
        else:
            self.status.showMessage(
                f"Reference path complete · {len(self.route_points)} points"
            )

    def undo_route_point(self) -> None:
        if not self.route_points:
            return
        self.route_points.pop()
        self._clear_envelope()
        self._render_route()
        self._update_export_state()
        self.status.showMessage(f"Reference path: {len(self.route_points)} points")

    def clear_route(self) -> None:
        self.route_points.clear()
        self._clear_envelope()
        self.draw_action.setChecked(False)
        self.set_drawing_mode(False)
        self._render_route()
        self._update_export_state()
        self.status.showMessage("Reference path cleared")

    def _clear_envelope(self) -> None:
        for item in self.envelope_items:
            self.view.scene.removeItem(item)
        self.envelope_items.clear()
        self.envelope_geometry = None

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
        pen = QPen(Qt.GlobalColor.darkRed)
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
            marker.setBrush(Qt.GlobalColor.darkRed)
            marker.setPen(marker_pen)
            marker.setZValue(101)
            marker.setToolTip(f"Point {index + 1}: {x:.3f}, {y:.3f}")
            self.view.scene.addItem(marker)
            self.route_items.append(marker)

    def calculate_envelope(self) -> None:
        if len(self.route_points) < 2:
            return
        vehicle = self._selected_vehicle()
        if not vehicle.supports_preview:
            QMessageBox.information(
                self,
                "Articulated geometry needed",
                "This vehicle needs a complete tractor, axle, trailer and coupling profile "
                "before a swept envelope can be calculated.",
            )
            return

        scale = self.units_spin.value()
        try:
            result = swept_footprint(
                self.route_points,
                self.length_spin.value() * scale,
                self.width_spin.value() * scale,
                max(0.25 * scale, 1e-6),
            )
        except Exception as error:
            QMessageBox.warning(self, "Could not calculate preview", str(error))
            return

        self.envelope_geometry = result
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        for polygon in polygons(result):
            self._add_ring(path, polygon.exterior.coords)
            for interior in polygon.interiors:
                self._add_ring(path, interior.coords)

        envelope = QGraphicsPathItem(path)
        envelope.setPen(QPen(QColor("#c77827"), 0))
        envelope.setBrush(QBrush(QColor(240, 166, 75, 85)))
        envelope.setZValue(50)
        self.view.scene.addItem(envelope)
        self.envelope_items.append(envelope)
        self.status.showMessage(
            f"Rigid-body footprint preview · {vehicle.name} · "
            f"{self.length_spin.value():.2f} m × {self.width_spin.value():.2f} m"
        )

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
        can_calculate = has_path and self._selected_vehicle().supports_preview
        self.calculate_action.setEnabled(can_calculate)

    def _save_route(self, target: str, include_base: bool) -> None:
        if self.drawing_path and Path(target).resolve() == self.drawing_path.resolve():
            raise ValueError("Choose a new output file so the source drawing is preserved.")
        if include_base and self.drawing_path:
            output = read_dxf(self.drawing_path)
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
        modelspace.add_lwpolyline(
            self.route_points,
            dxfattribs={"layer": "OASP_STEERING_PATH"},
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


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("OpenArch Swept Paths")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()






