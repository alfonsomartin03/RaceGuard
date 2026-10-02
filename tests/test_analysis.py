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

    def test_reports_use_screening_language(self) -> None:
        result = analyze(load_csv("examples/sample_race.csv"))
        text = result_to_text(result).lower()
        payload = result_to_dict(result)
        self.assertIn("not a finding", text)
        self.assertIn("official", payload["disclaimer"].lower())
        self.assertNotIn("cheated", text)

    def test_empty_input_is_safe(self) -> None:
        result = analyze([])
        self.assertEqual(result.segments, ())
        self.assertTrue(result.warnings)


if __name__ == "__main__":
    unittest.main()

