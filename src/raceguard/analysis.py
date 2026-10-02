"""Explainable single- and multi-rider screening pipeline."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from statistics import fmean

from .ingest import haversine_m
from .models import AnalysisConfig, Evidence, RiderProfile, SuspiciousSegment, TelemetryPoint
from .physics import expected_solo_power


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    points_analyzed: int
    riders_analyzed: tuple[str, ...]
    started_at: datetime | None
    ended_at: datetime | None
    segments: tuple[SuspiciousSegment, ...]
    warnings: tuple[str, ...] = ()

    @property
    def is_suspicious(self) -> bool:
        """Whether at least one segment crossed the configured review threshold."""

        return bool(self.segments)

    @property
    def confidence(self) -> float:
        """Highest explainable review score in the activity."""

        return max((segment.score for segment in self.segments), default=0.0)


@dataclass(frozen=True, slots=True)
class _Candidate:
    point: TelemetryPoint
    nearest_id: str | None
    separation_m: float | None
    expected_w: float | None
    deficit_w: float


def analyze(
    points: list[TelemetryPoint],
    profiles: dict[str, RiderProfile] | None = None,
    config: AnalysisConfig | None = None,
) -> AnalysisResult:
    config = config or AnalysisConfig()
    profiles = profiles or {}
    riders = tuple(sorted({point.rider_id for point in points}))
    warnings: list[str] = []
    if not points:
        return AnalysisResult(0, (), None, None, (), ("No valid telemetry points were supplied.",))
    if all(point.power_w is None for point in points):
        warnings.append("Power telemetry is absent; review scores rely on proximity and speed.")
    if len(riders) == 1:
        warnings.append(
            "Only one rider is present; screening uses sustained power-to-speed anomalies "
            "without rider-proximity evidence."
        )

    by_time: dict[datetime, list[TelemetryPoint]] = defaultdict(list)
    for point in points:
        by_time[point.timestamp].append(point)

    candidates: dict[tuple[str, str | None], list[_Candidate]] = defaultdict(list)
    for timestamp in sorted(by_time):
        samples = by_time[timestamp]
        for point in samples:
            nearest_id: str | None = None
            nearest_distance: float | None = None
            for other in samples:
                if other.rider_id == point.rider_id:
                    continue
                distance = haversine_m(point, other)
                if nearest_distance is None or distance < nearest_distance:
                    nearest_id, nearest_distance = other.rider_id, distance

            profile = profiles.get(point.rider_id, RiderProfile(point.rider_id))
            expected = expected_solo_power(
                point.speed_mps,
                point.gradient,
                profile,
                air_density_kg_m3=config.air_density_kg_m3,
                headwind_mps=config.wind_speed_mps,
            ) if point.power_w is not None else None
            deficit = max(0.0, (expected or 0.0) - (point.power_w or 0.0))
            close = nearest_distance is not None and nearest_distance <= config.proximity_threshold_m
            anomalous = (
                expected is not None
                and point.speed_mps >= config.minimum_speed_mps
                and deficit >= config.minimum_power_deficit_w
            )
            if close or anomalous:
                candidates[(point.rider_id, nearest_id if close else None)].append(
                    _Candidate(point, nearest_id if close else None, nearest_distance if close else None, expected, deficit)
                )

    segments: list[SuspiciousSegment] = []
    for (rider_id, nearest_id), rider_candidates in candidates.items():
        for run in _contiguous_runs(rider_candidates, config.maximum_sample_gap_seconds):
            duration = (run[-1].point.timestamp - run[0].point.timestamp).total_seconds()
            # One-Hz streams represent the interval following their last sample.
            sample_duration = duration + _typical_interval_seconds(run)
            if sample_duration < config.minimum_segment_seconds:
                continue
            segment = _build_segment(rider_id, nearest_id, run, sample_duration, config)
            if segment.score >= config.score_threshold:
                segments.append(segment)

    return AnalysisResult(
        points_analyzed=len(points),
        riders_analyzed=riders,
        started_at=min(point.timestamp for point in points),
        ended_at=max(point.timestamp for point in points),
        segments=tuple(sorted(segments, key=lambda segment: (-segment.score, segment.start_time))),
        warnings=tuple(warnings),
    )


def _contiguous_runs(items: list[_Candidate], max_gap: float) -> list[list[_Candidate]]:
    ordered = sorted(items, key=lambda item: item.point.timestamp)
    runs: list[list[_Candidate]] = []
    for item in ordered:
        if not runs or (item.point.timestamp - runs[-1][-1].point.timestamp).total_seconds() > max_gap:
            runs.append([item])
        else:
            runs[-1].append(item)
    return runs


def _typical_interval_seconds(run: list[_Candidate]) -> float:
    gaps = [
        (current.point.timestamp - previous.point.timestamp).total_seconds()
        for previous, current in zip(run, run[1:])
        if current.point.timestamp > previous.point.timestamp
    ]
    return min(fmean(gaps), 5.0) if gaps else 1.0


def _build_segment(
    rider_id: str,
    nearest_id: str | None,
    run: list[_Candidate],
    duration: float,
    config: AnalysisConfig,
) -> SuspiciousSegment:
    separations = [item.separation_m for item in run if item.separation_m is not None]
    powers = [item.point.power_w for item in run if item.point.power_w is not None]
    expected = [item.expected_w for item in run if item.expected_w is not None]
    deficits = [item.deficit_w for item in run]
    average_separation = fmean(separations) if separations else None
    average_deficit = fmean(deficits) if deficits else 0.0

    proximity_score = (
        max(0.0, 1.0 - average_separation / config.proximity_threshold_m)
        if average_separation is not None else 0.0
    )
    duration_score = min(1.0, duration / max(config.minimum_segment_seconds * 3, 1))
    power_score = min(1.0, average_deficit / max(config.minimum_power_deficit_w * 2, 1))
    average_speed = fmean(item.point.speed_mps for item in run)
    speed_score = min(1.0, average_speed / 15.0)
    telemetry_confidence = 0.45 + (0.25 if separations else 0) + (0.25 if powers else 0)
    telemetry_confidence = min(1.0, telemetry_confidence)
    weighted_evidence = 0.20 * duration_score + 0.15 * speed_score
    available_weight = 0.35
    if separations:
        weighted_evidence += 0.35 * proximity_score
        available_weight += 0.35
    if powers:
        weighted_evidence += 0.30 * power_score
        available_weight += 0.30
    # Missing proximity must reduce confidence, not make a single-rider flag impossible.
    score = (weighted_evidence / available_weight) * telemetry_confidence
    notes: list[str] = []
    if separations:
        notes.append("Sustained rider proximity was detected from synchronized GPS samples.")
    if average_deficit >= config.minimum_power_deficit_w:
        notes.append("Measured power was below the simplified solo-power estimate.")
    notes.append("Environmental effects and sensor error must be considered by an official.")

    return SuspiciousSegment(
        rider_id=rider_id,
        start_time=run[0].point.timestamp,
        end_time=run[-1].point.timestamp,
        score=round(score, 3),
        evidence=Evidence(
            proximity_score=round(proximity_score, 3),
            duration_score=round(duration_score, 3),
            power_score=round(power_score, 3),
            speed_score=round(speed_score, 3),
            telemetry_confidence=round(telemetry_confidence, 3),
            notes=tuple(notes),
        ),
        nearest_rider_id=nearest_id,
        average_separation_m=round(average_separation, 1) if average_separation is not None else None,
        average_speed_mps=round(average_speed, 2),
        average_power_w=round(fmean(powers), 1) if powers else None,
        expected_power_w=round(fmean(expected), 1) if expected else None,
        latitude=round(fmean(item.point.latitude for item in run), 6),
        longitude=round(fmean(item.point.longitude for item in run), 6),
        course_distance_m=_mean_optional(item.point.distance_m for item in run),
    )


def _mean_optional(values: Iterable[float | None]) -> float | None:
    available = [value for value in values if value is not None]
    return round(fmean(available), 1) if available else None
