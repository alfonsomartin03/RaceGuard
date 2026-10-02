"""Explainable single- and multi-rider screening pipeline."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from itertools import pairwise
from statistics import fmean, median

from .ingest import haversine_m
from .models import AnalysisConfig, Evidence, RiderProfile, SuspiciousSegment, TelemetryPoint
from .physics import GRAVITY_MPS2, expected_solo_power


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
    power_outlier_score: float
    adaptive_baseline: bool


@dataclass(frozen=True, slots=True)
class _PowerBaseline:
    profile: RiderProfile
    residual_center_w: float = 0.0
    residual_scale_w: float = 0.0
    adaptive: bool = False


@dataclass(frozen=True, slots=True)
class _RollingSignal:
    speed_mps: float
    gradient: float
    acceleration_mps2: float
    power_w: float | None
    stable_pedaling: bool


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

    rolling_signals = _build_rolling_signals(points, config)
    baselines = _build_power_baselines(points, profiles, config, rolling_signals)
    calibrated = [rider_id for rider_id, baseline in baselines.items() if baseline.adaptive]
    if calibrated:
        warnings.append(
            "Adaptive power-to-speed baselines were inferred from this activity for: "
            + ", ".join(calibrated)
            + "."
        )

    by_time: dict[datetime, list[TelemetryPoint]] = defaultdict(list)
    for point in points:
        by_time[point.timestamp].append(point)

    candidates: dict[tuple[str, str | None], list[_Candidate]] = defaultdict(list)
    for timestamp in sorted(by_time):
        samples = by_time[timestamp]
        for point in samples:
            signal = rolling_signals[(point.rider_id, point.timestamp)]
            nearest_id: str | None = None
            nearest_distance: float | None = None
            for other in samples:
                if other.rider_id == point.rider_id:
                    continue
                distance = haversine_m(point, other)
                if nearest_distance is None or distance < nearest_distance:
                    nearest_id, nearest_distance = other.rider_id, distance

            baseline = baselines[point.rider_id]
            profile = baseline.profile
            expected = expected_solo_power(
                signal.speed_mps,
                signal.gradient,
                profile,
                air_density_kg_m3=config.air_density_kg_m3,
                headwind_mps=config.wind_speed_mps,
                acceleration_mps2=signal.acceleration_mps2,
            ) if signal.stable_pedaling and signal.power_w is not None else None
            deficit = max(0.0, (expected or 0.0) - (signal.power_w or 0.0))
            outlier_z = (
                (deficit - baseline.residual_center_w) / baseline.residual_scale_w
                if baseline.adaptive and baseline.residual_scale_w > 0
                else 0.0
            )
            power_outlier_score = (
                min(1.0, max(0.0, outlier_z) / 4.0)
                if baseline.adaptive
                else min(1.0, deficit / max(config.minimum_power_deficit_w * 2, 1))
            )
            close = nearest_distance is not None and nearest_distance <= config.proximity_threshold_m
            anomalous = (
                expected is not None
                and signal.speed_mps >= config.minimum_speed_mps
                and deficit >= config.minimum_power_deficit_w
                and deficit / max(expected, 1.0) >= config.minimum_power_deficit_ratio
                and (
                    not baseline.adaptive
                    or outlier_z >= config.outlier_z_threshold
                )
            )
            if close or anomalous:
                candidates[(point.rider_id, nearest_id if close else None)].append(
                    _Candidate(
                        point,
                        nearest_id if close else None,
                        nearest_distance if close else None,
                        expected,
                        deficit,
                        power_outlier_score,
                        baseline.adaptive,
                    )
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
        for previous, current in pairwise(run)
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
    power_score = fmean(item.power_outlier_score for item in run)
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
        if any(item.adaptive_baseline for item in run):
            notes.append(
                "Power-to-speed efficiency was a sustained outlier from this rider's "
                "gradient-adjusted activity baseline."
            )
        else:
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


def _build_power_baselines(
    points: list[TelemetryPoint],
    supplied_profiles: dict[str, RiderProfile],
    config: AnalysisConfig,
    rolling_signals: dict[tuple[str, datetime], _RollingSignal],
) -> dict[str, _PowerBaseline]:
    """Infer a rider-specific aero baseline, then measure robust residual variation.

    Median CdA and median absolute deviation keep short anomalous periods from
    redefining what is normal for the rest of the activity.
    """

    by_rider: dict[str, list[TelemetryPoint]] = defaultdict(list)
    for point in points:
        by_rider[point.rider_id].append(point)

    baselines: dict[str, _PowerBaseline] = {}
    for rider_id, rider_points in by_rider.items():
        base_profile = supplied_profiles.get(rider_id, RiderProfile(rider_id))
        modeled_points = []
        for point in rider_points:
            signal = rolling_signals[(point.rider_id, point.timestamp)]
            if signal.stable_pedaling:
                modeled_points.append(
                    (
                        replace(
                            point,
                            speed_mps=signal.speed_mps,
                            power_w=signal.power_w,
                            gradient=signal.gradient,
                        ),
                        signal.acceleration_mps2,
                    )
                )
        inferred_cdas = [
            cda
            for point, acceleration in modeled_points
            if (cda := _infer_cda(point, base_profile, config, acceleration)) is not None
        ]
        if len(inferred_cdas) < config.minimum_baseline_points:
            baselines[rider_id] = _PowerBaseline(base_profile)
            continue

        adaptive_profile = replace(base_profile, cda_m2=median(inferred_cdas))
        residuals = [
            expected_solo_power(
                point.speed_mps,
                point.gradient,
                adaptive_profile,
                air_density_kg_m3=config.air_density_kg_m3,
                headwind_mps=config.wind_speed_mps,
                acceleration_mps2=acceleration,
            )
            - point.power_w
            for point, acceleration in modeled_points
            if point.power_w is not None and point.speed_mps >= config.minimum_speed_mps
        ]
        center = median(residuals)
        mad = median(abs(value - center) for value in residuals)
        baselines[rider_id] = _PowerBaseline(
            profile=adaptive_profile,
            residual_center_w=max(0.0, center),
            residual_scale_w=max(15.0, 1.4826 * mad),
            adaptive=True,
        )
    return baselines


def _build_rolling_signals(
    points: list[TelemetryPoint], config: AnalysisConfig
) -> dict[tuple[str, datetime], _RollingSignal]:
    """Smooth telemetry and exclude coasting or pedaling-transition windows."""

    by_rider: dict[str, list[TelemetryPoint]] = defaultdict(list)
    for point in points:
        by_rider[point.rider_id].append(point)

    signals: dict[tuple[str, datetime], _RollingSignal] = {}
    for rider_points in by_rider.values():
        ordered = sorted(rider_points, key=lambda item: item.timestamp)
        positive_powers = [
            point.power_w for point in ordered if point.power_w is not None and point.power_w > 0
        ]
        typical_power = median(positive_powers) if positive_powers else 0.0
        active_threshold = max(
            config.minimum_pedaling_power_w,
            typical_power * config.soft_pedaling_fraction,
        )
        active = [
            point.power_w is not None
            and point.power_w >= active_threshold
            and (
                point.cadence_rpm is None
                or point.cadence_rpm >= config.minimum_pedaling_cadence_rpm
            )
            for point in ordered
        ]
        inactive_prefix = [0]
        for is_active in active:
            inactive_prefix.append(inactive_prefix[-1] + (not is_active))

        smooth_left = smooth_right = transition_left = transition_right = 0
        half_window = config.rolling_window_seconds / 2
        for index, point in enumerate(ordered):
            while (point.timestamp - ordered[smooth_left].timestamp).total_seconds() > half_window:
                smooth_left += 1
            while (
                smooth_right < len(ordered)
                and (ordered[smooth_right].timestamp - point.timestamp).total_seconds() <= half_window
            ):
                smooth_right += 1
            while (
                point.timestamp - ordered[transition_left].timestamp
            ).total_seconds() > config.pedaling_transition_seconds:
                transition_left += 1
            while (
                transition_right < len(ordered)
                and (ordered[transition_right].timestamp - point.timestamp).total_seconds()
                <= config.pedaling_transition_seconds
            ):
                transition_right += 1

            window = ordered[smooth_left:smooth_right]
            powers = [item.power_w for item in window if item.power_w is not None]
            elapsed = (window[-1].timestamp - window[0].timestamp).total_seconds()
            acceleration = (
                (window[-1].speed_mps - window[0].speed_mps) / elapsed
                if elapsed > 0
                else 0.0
            )
            supplied_gradient = fmean(item.gradient for item in window)
            derived_gradient = _gradient_from_window(window, config)
            stable = (
                inactive_prefix[transition_right] - inactive_prefix[transition_left] == 0
            )
            signals[(point.rider_id, point.timestamp)] = _RollingSignal(
                speed_mps=fmean(item.speed_mps for item in window),
                gradient=(
                    supplied_gradient
                    if abs(supplied_gradient) > 0.0001 or derived_gradient is None
                    else derived_gradient
                ),
                acceleration_mps2=acceleration,
                power_w=fmean(powers) if powers else None,
                stable_pedaling=stable,
            )
    return signals


def _gradient_from_window(
    window: list[TelemetryPoint], config: AnalysisConfig
) -> float | None:
    """Derive road grade from smoothed elevation and traveled distance."""

    first, last = window[0], window[-1]
    if first.elevation_m is None or last.elevation_m is None:
        return None
    if first.distance_m is not None and last.distance_m is not None:
        traveled = last.distance_m - first.distance_m
    else:
        traveled = haversine_m(first, last)
    if traveled < config.minimum_gradient_distance_m:
        return None
    gradient = (last.elevation_m - first.elevation_m) / traveled
    return max(-0.25, min(0.25, gradient))


def _infer_cda(
    point: TelemetryPoint,
    profile: RiderProfile,
    config: AnalysisConfig,
    acceleration_mps2: float = 0.0,
) -> float | None:
    if point.power_w is None or point.speed_mps < config.minimum_speed_mps:
        return None
    air_speed = max(0.0, point.speed_mps + config.wind_speed_mps)
    if air_speed <= 0:
        return None
    wheel_power = point.power_w * profile.drivetrain_efficiency
    rolling_w = profile.crr * profile.total_mass_kg * GRAVITY_MPS2 * point.speed_mps
    climbing_w = profile.total_mass_kg * GRAVITY_MPS2 * point.gradient * point.speed_mps
    acceleration_w = profile.total_mass_kg * acceleration_mps2 * point.speed_mps
    aerodynamic_w = wheel_power - rolling_w - climbing_w - acceleration_w
    if aerodynamic_w <= 0:
        return None
    inferred = 2 * aerodynamic_w / (config.air_density_kg_m3 * air_speed**3)
    return inferred if 0.08 <= inferred <= 0.8 else None
