import io
import json
import unittest
from datetime import UTC, datetime, timedelta

from raceguard.models import TelemetryPoint
from raceguard.weather import (
    RequestLimitReached,
    _RequestLimiter,
    enrich_points_with_weather,
)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class WeatherTests(unittest.TestCase):
    def test_route_weather_is_batched_into_one_request(self) -> None:
        started = datetime(2031, 6, 1, 12, tzinfo=UTC)
        points = [
            TelemetryPoint(
                rider_id="wind",
                timestamp=started + timedelta(seconds=index),
                latitude=40.0 + index * 0.001,
                longitude=-74.0,
                speed_mps=12.0,
            )
            for index in range(30)
        ]
        requests = []

        def opener(request, timeout):
            requests.append(request.full_url)
            query = request.full_url.split("?", 1)[1]
            latitude_value = next(
                part.split("=", 1)[1] for part in query.split("&") if part.startswith("latitude=")
            )
            locations = latitude_value.count("%2C") + 1
            payload = [
                {
                    "latitude": 40.0 + index * 0.003,
                    "longitude": -74.0,
                    "hourly": {
                        "time": ["2031-06-01T12:00"],
                        "wind_speed_10m": [4.0],
                        "wind_direction_10m": [0.0],
                    },
                }
                for index in range(locations)
            ]
            return _Response(json.dumps(payload).encode())

        enriched, note = enrich_points_with_weather(points, opener=opener)

        self.assertEqual(len(requests), 1)
        self.assertLessEqual(requests[0].count("%2C") // 2 + 1, 12)
        self.assertTrue(all(point.wind_speed_mps == 4.0 for point in enriched))
        self.assertIn("Open-Meteo", note)

    def test_local_budget_blocks_before_provider_limit(self) -> None:
        limiter = _RequestLimiter()
        limiter._limits = ((60.0, 1), (3600.0, 2), (86400.0, 3))
        limiter.acquire(now=100.0, cost=1)

        with self.assertRaises(RequestLimitReached):
            limiter.acquire(now=101.0, cost=1)


if __name__ == "__main__":
    unittest.main()
