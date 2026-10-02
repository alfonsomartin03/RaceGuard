"""Rate-limited historical wind enrichment using Open-Meteo.

Weather is supporting context only. Modelled hourly wind cannot resolve local
gusts, shelter, vehicle wakes, or the aerodynamic effect of another rider.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import TelemetryPoint

OPEN_METEO_URL = os.getenv(
    "OPEN_METEO_BASE_URL",
    "https://historical-forecast-api.open-meteo.com/v1/forecast",
)
OPEN_METEO_API_KEY = os.getenv("OPEN_METEO_API_KEY")
OPEN_METEO_ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)."
MAX_ROUTE_SAMPLES = 12
MAX_WEATHER_RANGE_DAYS = 14
MAX_CACHE_ENTRIES = 256


class WeatherUnavailable(RuntimeError):
    """Raised when wind enrichment cannot safely be completed."""


class RequestLimitReached(WeatherUnavailable):
    """Raised before a request would exceed RaceGuard's configured budget."""


class _RequestLimiter:
    """Process-local rolling request budget with headroom below provider limits."""

    def __init__(self) -> None:
        self._requests: deque[float] = deque()
        self._lock = threading.Lock()
        self._limits = ((60.0, 500), (3600.0, 4000), (86400.0, 8000))

    def acquire(self, now: float | None = None, *, cost: int = 1) -> None:
        if cost < 1:
            raise ValueError("request cost must be positive")
        current = time.time() if now is None else now
        with self._lock:
            while self._requests and self._requests[0] <= current - 86400.0:
                self._requests.popleft()
            for window_seconds, maximum in self._limits:
                count = sum(value > current - window_seconds for value in self._requests)
                if count + cost > maximum:
                    raise RequestLimitReached(
                        "weather request budget reached; telemetry analysis continued without wind"
                    )
            self._requests.extend([current] * cost)


@dataclass(frozen=True, slots=True)
class _WeatherSeries:
    latitude: float
    longitude: float
    timestamps: tuple[datetime, ...]
    speeds_mps: tuple[float | None, ...]
    directions_deg: tuple[float | None, ...]


_limiter = _RequestLimiter()
_cache: dict[str, tuple[_WeatherSeries, ...]] = {}
_cache_lock = threading.Lock()


def enrich_points_with_weather(
    points: list[TelemetryPoint],
    *,
    opener: Callable[..., object] = urlopen,
) -> tuple[list[TelemetryPoint], str]:
    """Attach nearest hourly wind to telemetry using at most one API request.

    Up to twelve route locations are sent as a single Open-Meteo batch. Results
    are cached by URL because historical values for a fixed query are immutable
    for RaceGuard's purposes.
    """
    if not points:
        return points, "Weather was not requested because no telemetry was available."
    started = min(point.timestamp for point in points).astimezone(UTC)
    ended = max(point.timestamp for point in points).astimezone(UTC)
    if (ended.date() - started.date()).days + 1 > MAX_WEATHER_RANGE_DAYS:
        raise WeatherUnavailable(
            "weather lookup skipped because the upload spans more than "
            f"{MAX_WEATHER_RANGE_DAYS} days"
        )

    locations = _sample_locations(points, MAX_ROUTE_SAMPLES)
    parameters = {
        "latitude": ",".join(f"{latitude:.5f}" for latitude, _ in locations),
        "longitude": ",".join(f"{longitude:.5f}" for _, longitude in locations),
        "start_date": started.date().isoformat(),
        "end_date": ended.date().isoformat(),
        "hourly": "wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "ms",
        "timezone": "UTC",
    }
    if OPEN_METEO_API_KEY:
        parameters["apikey"] = OPEN_METEO_API_KEY
    query = urlencode(parameters)
    url = f"{OPEN_METEO_URL}?{query}"
    with _cache_lock:
        series = _cache.get(url)
    if series is None:
        # Open-Meteo may account for each requested location even though they
        # share one HTTP request, so reserve the conservative full batch cost.
        _limiter.acquire(cost=len(locations))
        request = Request(url, headers={"User-Agent": "RaceGuard/0.1"})
        try:
            with opener(request, timeout=5) as response:  # type: ignore[attr-defined]
                payload = json.load(response)
            series = _parse_response(payload)
        except WeatherUnavailable:
            raise
        except Exception as exc:
            raise WeatherUnavailable(f"weather service unavailable: {exc}") from exc
        with _cache_lock:
            if len(_cache) >= MAX_CACHE_ENTRIES:
                _cache.pop(next(iter(_cache)))
            _cache[url] = series

    enriched = [_apply_nearest_weather(point, series) for point in points]
    covered = sum(point.wind_speed_mps is not None for point in enriched)
    return enriched, f"{OPEN_METEO_ATTRIBUTION} Wind coverage: {covered}/{len(enriched)} points."


def _sample_locations(points: list[TelemetryPoint], maximum: int) -> list[tuple[float, float]]:
    ordered = sorted(points, key=lambda point: point.timestamp)
    if len(ordered) <= maximum:
        selected = ordered
    else:
        selected = [
            ordered[round(index * (len(ordered) - 1) / (maximum - 1))]
            for index in range(maximum)
        ]
    locations: list[tuple[float, float]] = []
    for point in selected:
        location = (round(point.latitude, 5), round(point.longitude, 5))
        if location not in locations:
            locations.append(location)
    return locations


def _parse_response(payload: object) -> tuple[_WeatherSeries, ...]:
    items = payload if isinstance(payload, list) else [payload]
    parsed: list[_WeatherSeries] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("hourly"), dict):
            raise WeatherUnavailable("weather service returned an unexpected response")
        hourly = item["hourly"]
        raw_times = hourly.get("time", [])
        speeds = hourly.get("wind_speed_10m", [])
        directions = hourly.get("wind_direction_10m", [])
        if not (len(raw_times) == len(speeds) == len(directions)):
            raise WeatherUnavailable("weather service returned incomplete wind arrays")
        parsed.append(
            _WeatherSeries(
                latitude=float(item["latitude"]),
                longitude=float(item["longitude"]),
                timestamps=tuple(_parse_utc(value) for value in raw_times),
                speeds_mps=tuple(_optional_number(value) for value in speeds),
                directions_deg=tuple(_optional_number(value) for value in directions),
            )
        )
    if not parsed:
        raise WeatherUnavailable("weather service returned no locations")
    return tuple(parsed)


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def _optional_number(value: object) -> float | None:
    return float(value) if value is not None else None


def _apply_nearest_weather(
    point: TelemetryPoint, series: tuple[_WeatherSeries, ...]
) -> TelemetryPoint:
    location = min(
        series,
        key=lambda candidate: _distance_squared(
            point.latitude, point.longitude, candidate.latitude, candidate.longitude
        ),
    )
    if not location.timestamps:
        return point
    index = min(
        range(len(location.timestamps)),
        key=lambda item: abs((location.timestamps[item] - point.timestamp).total_seconds()),
    )
    if abs((location.timestamps[index] - point.timestamp).total_seconds()) > 3600:
        return point
    return replace(
        point,
        wind_speed_mps=location.speeds_mps[index],
        wind_direction_deg=location.directions_deg[index],
    )


def _distance_squared(
    latitude: float, longitude: float, other_latitude: float, other_longitude: float
) -> float:
    longitude_scale = math.cos(math.radians((latitude + other_latitude) / 2))
    return (latitude - other_latitude) ** 2 + (
        (longitude - other_longitude) * longitude_scale
    ) ** 2
