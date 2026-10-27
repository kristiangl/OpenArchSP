import math
import unittest

from openarchsp.standard_profiles import standard_vehicle_geometries
from openarchsp.vehicle_kinematics import CombinationGeometry, simulate_combination


class StandardVehicleGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles = standard_vehicle_geometries()

    def test_catalogue_dimensions_and_steer_axles_are_valid(self):
        expected_lengths = {
            "passenger-vehicle-5-2": 5.2,
            "service-vehicle-8-8": 8.8,
            "single-unit-truck-bus-12-5": 12.5,
            "long-rigid-bus-14-5": 14.5,
            "passenger-vehicle-towing-trailer-17-6": 17.6,
            "prime-mover-semi-trailer-19": 19.0,
            "prime-mover-long-semi-trailer-25": 25.0,
            "b-double-25": 25.0,
            "b-double-26": 26.0,
            "b-triple-35-4": 35.4,
            "a-double-36-2": 36.2,
            "a-triple-53-4": 53.4,
        }
        self.assertEqual(set(expected_lengths), set(self.profiles))
        for key, expected_length in expected_lengths.items():
            with self.subTest(vehicle=key):
                geometry = self.profiles[key]
                geometry.validate()
                self.assertAlmostEqual(geometry.overall_length_m, expected_length, places=1)
                self.assertTrue(
                    any(
                        math.isclose(offset, geometry.steer_axle_m, abs_tol=1e-6)
                        for offset in geometry.units[0].axle_offsets_m
                    )
                )

    def test_b_triple_tracks_left_and_right_15m_turns(self):
        geometry = self.profiles["b-triple-35-4"]
        for route in (
            [(0, 0), (0, 60), (60, 60)],
            [(0, 0), (0, -60), (60, -60)],
        ):
            with self.subTest(route=route):
                result = simulate_combination(
                    route, geometry, corner_radius_m=15.0, spacing_m=0.5
                )
                self.assertTrue(result.path_complete)
                self.assertFalse(result.steering_rate_saturated)
                self.assertLess(result.maximum_articulation_deg, geometry.max_articulation_deg)
                self.assertLess(result.maximum_path_error_m, 1.0)
                self.assertGreater(max(result.offtracking_by_axle_m.values()), 0.0)
                self.assertEqual(len(result.units), 4)
                self.assertEqual(sum(map(len, (unit.axle_traces for unit in result.units))), 12)
                lead_steer_trace = result.units[0].axle_traces["Prime mover axle 1"]
                self.assertEqual(len(lead_steer_trace), len(result.steer_axle_path))
                self.assertLess(
                    max(math.dist(a, b) for a, b in zip(lead_steer_trace, result.steer_axle_path)),
                    1e-6,
                )

    def test_all_profiles_complete_a_30m_turn(self):
        route = [(0, 0), (0, 80), (80, 80)]
        for key, geometry in self.profiles.items():
            with self.subTest(vehicle=key):
                result = simulate_combination(
                    route, geometry, corner_radius_m=30.0, spacing_m=0.75
                )
                self.assertTrue(result.path_complete)
                self.assertLess(result.maximum_articulation_deg, geometry.max_articulation_deg)
                self.assertGreaterEqual(len(result.offtracking_by_axle_m), len(geometry.units))
                self.assertFalse(result.envelope.is_empty)

    def test_saved_geometry_accepts_dataclass_tuple_units(self):
        geometry = self.profiles["b-triple-35-4"]
        restored = CombinationGeometry.from_dict(geometry.to_dict())
        self.assertEqual(restored, geometry)


if __name__ == "__main__":
    unittest.main()
