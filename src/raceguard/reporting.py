"""Serialization and human-readable reports."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .analysis import AnalysisResult


def result_to_dict(result: AnalysisResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "disclaimer": "Screening result only. A qualified official must review all evidence.",
        "summary": {
            "points_analyzed": result.points_analyzed,
            "riders_analyzed": list(result.riders_analyzed),
            "started_at": result.started_at.isoformat() if result.started_at else None,
            "ended_at": result.ended_at.isoformat() if result.ended_at else None,
            "segments_for_review": len(result.segments),
            "is_suspicious": result.is_suspicious,
            "confidence": result.confidence,
            "assessment": "review_recommended" if result.is_suspicious else "no_flags_detected",
        },
        "warnings": list(result.warnings),
        "segments": [],
    }
    for segment in result.segments:
        item = asdict(segment)
        item["start_time"] = segment.start_time.isoformat()
        item["end_time"] = segment.end_time.isoformat()
        item["duration_seconds"] = segment.duration_seconds
        payload["segments"].append(item)
    return payload


def result_to_text(result: AnalysisResult) -> str:
    lines = [
        "RACEGUARD TELEMETRY SCREENING",
        "Screening result only — not a finding of a drafting violation.",
        "",
        f"Points: {result.points_analyzed} | Riders: {len(result.riders_analyzed)} | Review segments: {len(result.segments)}",
        f"Assessment: {'REVIEW RECOMMENDED' if result.is_suspicious else 'NO FLAGS DETECTED'} | Confidence: {result.confidence:.0%}",
    ]
    for warning in result.warnings:
        lines.append(f"Warning: {warning}")
    for index, segment in enumerate(result.segments, start=1):
        lines.extend(
            [
                "",
                f"{index}. RIDER {segment.rider_id} — REVIEW PRIORITY {segment.score:.0%}",
                f"   {segment.start_time.isoformat()} to {segment.end_time.isoformat()}",
                f"   Duration: {segment.duration_seconds:.0f}s | Speed: {(segment.average_speed_mps or 0) * 3.6:.1f} km/h",
                f"   Nearest rider: {segment.nearest_rider_id or 'unavailable'} | Separation: {_number(segment.average_separation_m, 'm')}",
                f"   Power: {_number(segment.average_power_w, 'W')} | Expected solo: {_number(segment.expected_power_w, 'W')}",
                f"   Location: {_location(segment.latitude, segment.longitude, segment.course_distance_m)}",
                "   Recommendation: review footage, observations, weather, and source telemetry.",
            ]
        )
    return "\n".join(lines)


def _number(value: float | None, suffix: str) -> str:
    return f"{value:.1f} {suffix}" if value is not None else "unavailable"


def _location(latitude: float | None, longitude: float | None, distance_m: float | None) -> str:
    coordinates = (
        f"{latitude:.6f}, {longitude:.6f}"
        if latitude is not None and longitude is not None
        else "unavailable"
    )
    if distance_m is not None:
        return f"{coordinates} ({distance_m / 1000:.2f} km into activity)"
    return coordinates
