import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ezdxf
from PySide6.QtCore import QSettings, Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from openarchsp.app import MainWindow


class EditorHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        prefs = QSettings(str(self.folder / "settings.ini"), QSettings.Format.IniFormat)
        self.preferences = patch("openarchsp.app.QSettings", return_value=prefs)
        self.preferences.start()
        self.addCleanup(self.preferences.stop)
        self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)
        source = self.folder / "site.dxf"
        drawing = ezdxf.new()
        drawing.units = 6
        drawing.modelspace().add_line((0, 0), (40, 40))
        drawing.saveas(source)
        self.window.load_drawing(str(source))

    def tearDown(self):
        self.window.preview_timer.stop()
        self.window._preview_generation += 1
        self.window.preview_pool.waitForDone()
        self.app.processEvents()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
            self.window.close()

    def test_add_rename_remove_path_each_undo_and_redo(self):
        w = self.window
        original_id = w.active_path_id
        w.add_route_point(0, 0)
        w.add_route_point(20, 0)
        original = tuple(w.route_points)
        w.new_swept_path()
        second_id = w.active_path_id
        self.assertEqual(len(w.swept_paths), 2)
        w.undo_action.trigger()
        self.assertEqual([p.id for p in w.swept_paths], [original_id])
        w.redo_action.trigger()
        self.assertEqual(w.active_path_id, second_id)
        with patch.object(QInputDialog, "getText", return_value=("Delivery route", True)):
            w.rename_swept_path()
        w.undo_action.trigger()
        self.assertEqual(w.active_path.name, "Swept path 2")
        w.redo_action.trigger()
        self.assertEqual(w.active_path.name, "Delivery route")
        w.add_route_point(2, 3)
        w.add_route_point(15, 3)
        w.speed_spin.setValue(3.75)
        w.active_path.vehicle_stations.append(0.3)
        w._record_edit("Place vehicle")
        removed_settings = w.active_path.settings
        w.remove_swept_path()
        self.assertEqual(w.active_path_id, original_id)
        w.undo_action.trigger()
        self.assertEqual(w.active_path_id, second_id)
        self.assertEqual(w.active_path.name, "Delivery route")
        self.assertEqual(w.route_points, [(2, 3), (15, 3)])
        self.assertEqual(w.active_path.settings, removed_settings)
        self.assertEqual(w.active_path.vehicle_stations, [0.3])
        w.redo_action.trigger()
        self.assertEqual(tuple(w.route_points), original)
        self.assertEqual(len(w.swept_paths), 1)

    def test_drag_is_one_edit_and_new_edit_discards_redo(self):
        w = self.window
        w.add_route_point(0, 0)
        w.add_route_point(20, 0)
        count = w.undo_stack.count()
        w._move_waypoint(1, 18, 1, True)
        w._move_waypoint(1, 17, 2, True)
        w._move_waypoint(1, 16, 3, False)
        self.assertEqual(w.undo_stack.count(), count + 1)
        w.undo_action.trigger()
        self.assertEqual(w.route_points[-1], (20, 0))
        w.redo_action.trigger()
        self.assertEqual(w.route_points[-1], (16, 3))
        w.undo_action.trigger()
        w.add_route_point(30, 0)
        self.assertFalse(w.undo_stack.canRedo())

    def test_clear_and_remove_last_path_restore_all_points(self):
        w = self.window
        w.add_route_point(0, 0)
        w.add_route_point(20, 0)
        w.remove_swept_path()
        self.assertEqual(w.route_points, [])
        w.undo_action.trigger()
        self.assertEqual(w.route_points, [(0, 0), (20, 0)])
        w.redo_action.trigger()
        self.assertEqual(w.route_points, [])

    def test_vehicle_and_numeric_changes_restore_previous_settings(self):
        w = self.window
        vehicle = w.active_path.vehicle_id
        speed = w.speed_spin.value()
        w.speed_spin.setValue(3.7)
        w.undo_action.trigger()
        self.assertEqual(w.speed_spin.value(), speed)
        w.redo_action.trigger()
        self.assertEqual(w.speed_spin.value(), 3.7)
        w.vehicle_combo.setCurrentIndex(w.vehicle_combo.findData("custom-rigid"))
        w.undo_action.trigger()
        self.assertEqual(w.active_path.vehicle_id, vehicle)
        w.redo_action.trigger()
        self.assertEqual(w.active_path.vehicle_id, "custom-rigid")

    def test_save_keeps_history_and_open_resets_it(self):
        w = self.window
        w.add_route_point(0, 0)
        w.project_path = self.folder / "saved.oasp"
        self.assertTrue(w.save_editable_project())
        w.add_route_point(20, 0)
        w.undo_action.trigger()
        self.assertEqual(w._edit_state(), w._saved_state)
        self.assertTrue(w.undo_stack.canUndo())
        w.load_drawing(str(w.project_path))
        self.assertFalse(w.undo_stack.canUndo())
        self.assertFalse(w.undo_stack.canRedo())

    def test_double_click_finish_and_resume_without_duplicate_point(self):
        w = self.window
        w.show()
        self.app.processEvents()
        w.draw_action.trigger()
        QTest.mouseClick(w.view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(150, 150))
        QTest.mouseClick(w.view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(250, 150))
        QTest.mouseDClick(w.view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(250, 150))
        self.assertFalse(w.view.drawing_mode)
        self.assertEqual(len(w.route_points), 2)
        w.draw_action.trigger()
        self.assertTrue(w.view.drawing_mode)
        self.assertEqual(len(w.route_points), 2)
        QTest.mouseClick(w.view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(350, 150))
        QTest.keyClick(w.view, Qt.Key.Key_Escape)
        self.assertFalse(w.view.drawing_mode)
        self.assertEqual(len(w.route_points), 3)

    def test_undo_redo_shortcuts(self):
        w = self.window
        w.show()
        self.app.processEvents()
        w.add_route_point(1, 2)
        w.add_route_point(3, 4)
        w.view.setFocus()
        QTest.keyClick(w.view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(w.route_points, [(1, 2)])
        QTest.keyClick(w.view, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(w.route_points, [(1, 2), (3, 4)])

    def test_undo_path_removal_after_save_keeps_project_editable(self):
        w = self.window
        w.add_route_point(0, 0)
        w.add_route_point(20, 0)
        w.new_swept_path()
        w.add_route_point(0, 10)
        w.add_route_point(20, 10)
        w.remove_swept_path()
        w.undo_action.trigger()
        w.project_path = self.folder / "undone.oasp"
        w.save_editable_project()
        w.load_drawing(str(w.project_path))
        self.assertEqual(len(w.swept_paths), 2)
        self.assertEqual(w.route_points, [(0, 10), (20, 10)])

    def test_start_screen_return_preserves_open_drawing_and_history(self):
        w = self.window
        w.add_route_point(0, 0)
        count = w.undo_stack.count()
        w._show_start_screen()
        self.assertIs(w.pages.currentWidget(), w.start_screen)
        self.assertTrue(w.start_screen.return_button.isHidden() is False)
        w._show_editor()
        self.assertIs(w.pages.currentWidget(), w.view)
        self.assertEqual(w.route_points, [(0, 0)])
        self.assertEqual(w.undo_stack.count(), count)
