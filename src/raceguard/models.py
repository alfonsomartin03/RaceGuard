"""Core domain models shared by ingestion, analysis, and presentation layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class RiderProfile:
    """Physical assumptions used to estimate solo power demand."""

    rider_id: str
    rider_mass_kg: float = 75.0
    bike_mass_kg: float = 8.0
    cda_m2: float = 0.30
    crr: float = 0.004
    drivetrain_efficiency: float = 0.97

    @property
    def total_mass_kg(self) -> float:
        return self.rider_mass_kg + self.bike_mass_kg


@dataclass(frozen=True, slots=True)
class TelemetryPoint:
    """A normalized telemetry sample in SI units."""

    rider_id: str
    timestamp: datetime
    latitude: float
    longitude: float
    speed_mps: float
    power_w: float | None = None
    elevation_m: float | None = None
    cadence_rpm: float | None = None
    heart_rate_bpm: float | None = None
    distance_m: float | None = None
    gradient: float = 0.0


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    """Conservative defaults for surfacing segments for human review."""

    air_density_kg_m3: float = 1.225
    wind_speed_mps: float = 0.0
    proximity_threshold_m: float = 15.0
    minimum_segment_seconds: float = 15.0
    minimum_power_deficit_w: float = 45.0
    minimum_speed_mps: float = 8.0
    maximum_sample_gap_seconds: float = 5.0
    score_threshold: float = 0.55


@dataclass(frozen=True, slots=True)
class Evidence:
    """Explainable measurements contributing to a review flag."""

    proximity_score: float = 0.0
    duration_score: float = 0.0
    power_score: float = 0.0
    speed_score: float = 0.0
    telemetry_confidence: float = 0.0
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class SuspiciousSegment:
    """A time range warranting review, never an assertion of wrongdoing."""

    rider_id: str
    start_time: datetime
    end_time: datetime
    score: float
    evidence: Evidence
    nearest_rider_id: str | None = None
    average_separation_m: float | None = None
    average_speed_mps: float | None = None
    average_power_w: float | None = None
    expected_power_w: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    course_distance_m: float | None = None

    @property
    def duration_seconds(self) -> float:
        return (self.end_time - self.start_time).total_seconds()
