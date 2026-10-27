"""Application entry point for OpenArch Swept Paths."""

from __future__ import annotations

import sys
from pathlib import Path

import ezdxf
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QPainter, QPainterPath, QPen, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGraphicsEllipseItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QToolBar,
)

from openarchsp.drawing import draw_document, read_dxf


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
        self.resize(1200, 800)
        self.document = None
        self.drawing_path: Path | None = None
        self.route_points: list[tuple[float, float]] = []
        self.route_items: list[object] = []

        self.view = DrawingView()
        self.setCentralWidget(self.view)
        self.view.worldClicked.connect(self.add_route_point)
        self.view.finishRequested.connect(self.finish_route)
        self._build_actions()

        self.status = QStatusBar()
        self.status.showMessage("Open a DXF site drawing to begin")
        self.setStatusBar(self.status)

    def _build_actions(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self.open_action = QAction("&Open DXF…", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.open_dxf)
        file_menu.addAction(self.open_action)

        self.export_action = QAction("Export path as DXF…", self)
        self.export_action.triggered.connect(self.export_path)
        self.export_action.setEnabled(False)
        file_menu.addAction(self.export_action)

        self.save_copy_action = QAction("Save drawing with path as…", self)
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

        self.draw_action = QAction("Draw steering path", self)
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
        # draw_document() clears the scene and destroys old overlay items.
        self.route_items.clear()
        self.route_points.clear()
        self.draw_action.setChecked(False)
        self.set_drawing_mode(False)
        self._render_route()
        self._update_export_state()
        self.draw_action.setEnabled(True)
        self.setWindowTitle(f"OpenArch Swept Paths — {self.drawing_path.name}")
        self.status.showMessage(
            f"{self.drawing_path} · {count} model-space entities rendered"
        )
        self.view.fit_drawing()

    def set_drawing_mode(self, enabled: bool) -> None:
        self.view.drawing_mode = enabled
        self.finish_action.setEnabled(enabled)
        if enabled:
            self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.view.setFocus()
            self.status.showMessage(
                "Click route points in order · Right-click or Enter to finish · Esc to stop drawing"
            )
        else:
            self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.status.showMessage("Path edit mode off · Wheel zooms · Middle/left drag pans")

    def add_route_point(self, scene_x: float, scene_y: float) -> None:
        self.route_points.append((scene_x, scene_y))
        self._render_route()
        self._update_export_state()
        self.status.showMessage(f"Steering path: {len(self.route_points)} points")

    def finish_route(self) -> None:
        self.draw_action.setChecked(False)
        self.set_drawing_mode(False)
        if len(self.route_points) < 2:
            self.status.showMessage("Add at least two points to define a steering path")
        else:
            self.status.showMessage(
                f"Steering path complete · {len(self.route_points)} points · Ready to export"
            )

    def undo_route_point(self) -> None:
        if not self.route_points:
            return
        self.route_points.pop()
        self._render_route()
        self._update_export_state()
        self.status.showMessage(f"Steering path: {len(self.route_points)} points")

    def clear_route(self) -> None:
        self.route_points.clear()
        self.draw_action.setChecked(False)
        self.set_drawing_mode(False)
        self._render_route()
        self._update_export_state()
        self.status.showMessage("Steering path cleared")

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

    def _update_export_state(self) -> None:
        ready = self.document is not None and len(self.route_points) >= 2
        self.export_action.setEnabled(ready)
        self.save_copy_action.setEnabled(ready)

    def _save_route(self, target: str, include_base: bool) -> None:
        if self.drawing_path and Path(target).resolve() == self.drawing_path.resolve():
            raise ValueError("Choose a new output file so the source drawing is preserved.")
        if include_base and self.drawing_path:
            output = read_dxf(self.drawing_path)
        else:
            output = ezdxf.new("R2010")
            if self.document is not None:
                output.header["$INSUNITS"] = self.document.header.get("$INSUNITS", 0)

        layer_name = "OASP_STEERING_PATH"
        if layer_name not in output.layers:
            output.layers.new(layer_name, dxfattribs={"color": 1, "lineweight": 35})
        output.modelspace().add_lwpolyline(
            self.route_points,
            dxfattribs={"layer": layer_name},
        )
        output.saveas(target)

    def export_path(self) -> None:
        if len(self.route_points) < 2:
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "Export steering path", "steering_path.dxf", "DXF drawings (*.dxf)"
        )
        if not target:
            return
        try:
            self._save_route(target, include_base=False)
        except Exception as error:
            QMessageBox.critical(self, "Could not export path", str(error))
            return
        self.status.showMessage(f"Steering path exported to {target}")

    def save_drawing_copy(self) -> None:
        if len(self.route_points) < 2:
            return
        default_name = (
            f"{self.drawing_path.stem}_with_path.dxf"
            if self.drawing_path
            else "site_with_path.dxf"
        )
        target, _ = QFileDialog.getSaveFileName(
            self, "Save drawing copy with path", default_name, "DXF drawings (*.dxf)"
        )
        if not target:
            return
        try:
            self._save_route(target, include_base=True)
        except Exception as error:
            QMessageBox.critical(self, "Could not save drawing copy", str(error))
            return
        self.status.showMessage(f"Drawing copy saved to {target}")


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("OpenArch Swept Paths")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()






