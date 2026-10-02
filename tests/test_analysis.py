import unittest
from datetime import UTC, datetime, timedelta

from raceguard.analysis import analyze
from raceguard.ingest import load_csv
from raceguard.models import RiderProfile, TelemetryPoint
from raceguard.physics import expected_solo_power
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

    def test_review_locations_are_chronological(self) -> None:
        result = analyze(load_csv("examples/sample_race.csv"))
        starts = [segment.start_time for segment in result.segments]

        self.assertEqual(starts, sorted(starts))

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

    def test_aero_rider_is_calibrated_from_activity_baseline(self) -> None:
        profile = RiderProfile("aero", cda_m2=0.15)
        points = self._activity_points(profile, outlier_samples=0, baseline_speed=16.0)

        result = analyze(points)

        self.assertFalse(result.is_suspicious)
        self.assertTrue(any("Adaptive power-to-speed" in warning for warning in result.warnings))

    def test_sustained_speed_outlier_is_flagged_against_rider_baseline(self) -> None:
        profile = RiderProfile("aero", cda_m2=0.18)
        points = self._activity_points(profile, outlier_samples=16)

        result = analyze(points)

        self.assertTrue(result.is_suspicious)
        self.assertGreaterEqual(result.confidence, 0.55)
        self.assertGreater(result.segments[0].expected_power_w, result.segments[0].average_power_w)

    def test_coasting_at_high_speed_is_not_flagged(self) -> None:
        profile = RiderProfile("coasting", cda_m2=0.18)
        points = self._activity_points(profile, outlier_samples=0)
        started = points[0].timestamp
        points.extend(
            TelemetryPoint(
                rider_id=profile.rider_id,
                timestamp=started + timedelta(seconds=60 + index),
                latitude=40.006 + index * 0.0001,
                longitude=-74.0,
                speed_mps=15.0,
                power_w=0.0,
                cadence_rpm=0.0,
                gradient=-0.02,
            )
            for index in range(25)
        )

        result = analyze(points)

        self.assertFalse(result.is_suspicious)

    def test_short_soft_pedaling_and_restart_do_not_create_flag(self) -> None:
        profile = RiderProfile("transition", cda_m2=0.18)
        points = self._activity_points(profile, outlier_samples=0)
        started = points[0].timestamp
        baseline_power = points[0].power_w
        for index in range(16):
            restarting = index >= 8
            points.append(
                TelemetryPoint(
                    rider_id=profile.rider_id,
                    timestamp=started + timedelta(seconds=60 + index),
                    latitude=40.006 + index * 0.0001,
                    longitude=-74.0,
                    speed_mps=15.0,
                    power_w=baseline_power if restarting else 40.0,
                    cadence_rpm=85.0 if restarting else 20.0,
                    gradient=0.01,
                )
            )

        result = analyze(points)

        self.assertFalse(result.is_suspicious)

    def test_derived_downhill_gradient_prevents_fast_descent_flag(self) -> None:
        profile = RiderProfile("descender", cda_m2=0.18)
        started = datetime(2026, 6, 1, tzinfo=UTC)
        flat_power = expected_solo_power(15.0, 0.0, profile)
        downhill_power = expected_solo_power(15.0, -0.01, profile)
        points: list[TelemetryPoint] = []
        for index in range(85):
            descending = index >= 60
            descent_distance = max(0.0, (index - 59) * 15.0)
            points.append(
                TelemetryPoint(
                    rider_id=profile.rider_id,
                    timestamp=started + timedelta(seconds=index),
                    latitude=40.0 + index * 0.0001,
                    longitude=-74.0,
                    speed_mps=15.0,
                    power_w=downhill_power if descending else flat_power,
                    elevation_m=100.0 - 0.01 * descent_distance,
                    distance_m=index * 15.0,
                    cadence_rpm=90.0,
                    # FIT records do not include grade; analysis must derive it.
                    gradient=0.0,
                )
            )

        result = analyze(points)

        self.assertFalse(result.is_suspicious)

    @staticmethod
    def _activity_points(
        profile: RiderProfile, outlier_samples: int, baseline_speed: float = 12.0
    ) -> list[TelemetryPoint]:
        started = datetime(2026, 6, 1, tzinfo=UTC)
        baseline_samples = 60
        baseline_power = expected_solo_power(baseline_speed, 0.01, profile)
        points = [
            TelemetryPoint(
                rider_id=profile.rider_id,
                timestamp=started + timedelta(seconds=index),
                latitude=40.0 + index * 0.0001,
                longitude=-74.0,
                speed_mps=baseline_speed,
                power_w=baseline_power,
                gradient=0.01,
            )
            for index in range(baseline_samples)
        ]
        points.extend(
            TelemetryPoint(
                rider_id=profile.rider_id,
                timestamp=started + timedelta(seconds=baseline_samples + index),
                latitude=40.006 + index * 0.0001,
                longitude=-74.0,
                speed_mps=15.0,
                power_w=baseline_power,
                gradient=0.01,
            )
            for index in range(outlier_samples)
        )
        return points


if __name__ == "__main__":
    unittest.main()
