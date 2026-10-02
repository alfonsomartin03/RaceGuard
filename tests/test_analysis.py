import unittest

from raceguard.analysis import analyze
from raceguard.ingest import load_csv
from raceguard.reporting import result_to_dict, result_to_text


class AnalysisTests(unittest.TestCase):
    def test_sample_race_surfaces_reviewable_segments(self) -> None:
        result = analyze(load_csv("examples/sample_race.csv"))
        rider_142 = [segment for segment in result.segments if segment.rider_id == "142"]
        self.assertTrue(rider_142)
        self.assertEqual(rider_142[0].nearest_rider_id, "138")
        self.assertLess(rider_142[0].average_separation_m or 100, 15)
        self.assertAlmostEqual(rider_142[0].latitude or 0, 40.00125, places=5)
        self.assertAlmostEqual(rider_142[0].longitude or 1, -74.0, places=5)
        self.assertTrue(result.is_suspicious)
        self.assertGreater(result.confidence, 0.5)

    def test_reports_use_screening_language(self) -> None:
        result = analyze(load_csv("examples/sample_race.csv"))
        text = result_to_text(result).lower()
        payload = result_to_dict(result)
        self.assertIn("not a finding", text)
        self.assertIn("official", payload["disclaimer"].lower())
        self.assertNotIn("cheated", text)
        self.assertTrue(payload["summary"]["is_suspicious"])
        self.assertEqual(payload["summary"]["assessment"], "review_recommended")
        self.assertIn("latitude", payload["segments"][0])
        self.assertIn("Location:", result_to_text(result))

    def test_empty_input_is_safe(self) -> None:
        result = analyze([])
        self.assertEqual(result.segments, ())
        self.assertTrue(result.warnings)
        self.assertFalse(result.is_suspicious)
        self.assertEqual(result.confidence, 0.0)

    def test_single_rider_can_be_flagged_from_power_speed_anomaly(self) -> None:
        points = [
            point for point in load_csv("examples/sample_race.csv") if point.rider_id == "142"
        ]

        result = analyze(points)

        self.assertTrue(result.is_suspicious)
        self.assertEqual(len(result.segments), 1)
        self.assertIsNone(result.segments[0].nearest_rider_id)
        self.assertGreaterEqual(result.confidence, 0.55)
        self.assertIn("power-to-speed anomalies", result.warnings[0])


if __name__ == "__main__":
    unittest.main()
