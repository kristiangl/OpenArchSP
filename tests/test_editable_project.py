import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ezdxf
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from openarchsp.app import MainWindow
from openarchsp.swept_path import swept_footprint


class EditableProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_joined_footprint_spans_the_space_between_poses(self):
        envelope = swept_footprint([(0, 0), (20, 0)], 4, 2, spacing=10)
        self.assertEqual(envelope.geom_type, "Polygon")
        self.assertAlmostEqual(envelope.area, 48)

    def test_portable_round_trip_edit_and_both_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            prefs = QSettings(str(folder / "settings.ini"), QSettings.Format.IniFormat)
            with patch("openarchsp.app.QSettings", return_value=prefs):
                window = MainWindow()
                self.addCleanup(window.deleteLater)
                original = folder / "site.dxf"
                project = folder / "test.oasp"
                doc = ezdxf.new()
                doc.units = 6
                doc.modelspace().add_line((0, 0), (40, 40))
                doc.saveas(original)
                window.load_drawing(str(original))
                window.active_path.name = "Entry"
                window.route_points.extend([(0, 0), (0, 20), (20, 20)])
                window.active_path.vehicle_stations.append(0.4)
                window.speed_spin.setValue(3.5)
                window.new_swept_path()
                window.vehicle_combo.setCurrentIndex(window.vehicle_combo.findData("custom-rigid"))
                window.length_spin.setValue(8.125)
                window.active_path.name = "Exit"
                window.route_points.extend([(0, 10), (30, 10)])
                window.wheels_visible.setChecked(False)
                with patch.object(QFileDialog, "getSaveFileName", return_value=(str(project), "")):
                    self.assertTrue(window.save_editable_project())
                original.unlink()
                window.load_drawing(str(project))
                self.assertEqual(window.active_path.name, "Exit")
                self.assertEqual(window.length_spin.value(), 8.125)
                self.assertFalse(window.wheels_visible.isChecked())
                self.assertEqual(len(window.document.modelspace()), 1)
                for path in window.swept_paths:
                    self.assertIsNotNone(path.envelope_geometry)
                window.path_combo.setCurrentIndex(0)
                self.assertEqual(window.speed_spin.value(), 3.5)
                self.assertEqual(window.active_path.vehicle_stations, [0.4])
                self.assertEqual(window.route_points[2], (20, 20))
                for base in (False, True):
                    target = folder / f"export-{base}.dxf"
                    window._save_route(str(target), include_base=base)
                    model = ezdxf.readfile(target).modelspace()
                    self.assertEqual(len(model.query('LINE[layer=="0"]')), int(base))
                    for index in (1, 2):
                        self.assertTrue(model.query(f'LWPOLYLINE[layer=="OASP-Outside-P{index}"]'))
                window._move_waypoint(2, 19, 21, False)
                self.assertTrue(window.save_editable_project())
                window.load_drawing(str(project))
                self.assertEqual(window.route_points[2], (19, 21))
                bad = folder / "bad.oasp"
                with ZipFile(bad, "w") as archive:
                    archive.writestr("project.json", '{"version": 999}')
                with patch.object(QMessageBox, "critical") as error:
                    window.load_drawing(str(bad))
                    error.assert_called_once()
                self.assertEqual(window.route_points[2], (19, 21))
                window.close()
