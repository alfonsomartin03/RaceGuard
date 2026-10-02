# ITT Draft Detection System

A telemetry-based race-integrity analysis system for identifying sections of individual time trials that may warrant review for possible drafting.

## Overview

Individual time trials rely on riders maintaining required separation distances, but race officials cannot continuously observe every competitor across an entire course.

This project explores whether rider telemetry can help officials identify portions of a race that are statistically or physically consistent with possible drafting.

The system does **not** automatically determine whether a rider committed a drafting violation.

Instead, it acts as a screening system:

    Race Telemetry
          ↓
    Automated Analysis
          ↓
    Suspicious Segment Detection
          ↓
    Evidence / Confidence Report
          ↓
    Commissaire Review

The goal is to help officials determine **where to look**, not replace human judgment.

## Input Data

Initial versions would analyze FIT files containing:

- GPS coordinates
- Timestamp
- Speed
- Power
- Cadence
- Heart rate
- Elevation

Additional optional information could include:

- Rider mass
- Bike mass
- CdA
- Crr
- Weather
- Wind direction
- Wind speed
- Course data
- Rider start time

Analyzing multiple riders from the same race would substantially improve detection capability.

## Detection Concept

Drafting reduces the aerodynamic power required to maintain a given velocity.

The system can search for periods where observed rider behavior differs significantly from what would normally be expected from:

    Power
    Speed
    Gradient
    Wind
    Rider characteristics

Example:

    Segment: 31:42–32:17

    Duration:             35 s
    Average speed:        47.8 km/h
    Average power:        238 W
    Expected solo power:  327 W

    Estimated discrepancy: -89 W

    Classification:
    Segment warrants review

This would NOT automatically mean drafting occurred.

Possible alternative explanations could include:

- Tailwind
- GPS error
- Incorrect CdA estimate
- Road surface
- Sensor error
- Vehicle interference
- Incorrect elevation data

## Multi-Rider Detection

When multiple competitors provide telemetry, the system becomes much more powerful.

Rider trajectories can be reconstructed using:

    latitude
    longitude
    timestamp

Start-time differences can then be normalized into absolute race time.

The system could search for periods where:

    Rider A
       ↓
    < proximity threshold
       ↓
    Rider B

for an extended duration.

Potential evidence:

    Rider separation
    Relative velocity
    Duration
    Power reduction
    Speed similarity
    Course position

## Detection Pipeline

    FIT Files
        │
        ▼
    FIT Parser
        │
        ▼
    Data Cleaning
        │
        ▼
    Timestamp Alignment
        │
        ▼
    GPS Trajectory Reconstruction
        │
        ▼
    Rider Proximity Analysis
        │
        ├─────────────┐
        ▼             ▼
    Physics Model   Power Analysis
        │             │
        └──────┬──────┘
               ▼
        Anomaly Detection
               │
               ▼
        Suspicious Segments
               │
               ▼
        Commissaire Dashboard

## Physics Model

Expected solo power can be approximated using:

    P =
    ½ρCdA·v³
    + Crr·m·g·v
    + m·g·v·sin(θ)

Additional models can account for:

- Headwind
- Tailwind
- Crosswind
- Acceleration
- Drivetrain losses

The difference between measured power and estimated solo power can then be examined.

    ΔP = P_measured - P_expected

Large sustained discrepancies could become one component of the detection model.

## Suspicion Model

The system should combine multiple signals rather than using a single threshold.

Example:

    Draft Evidence Score

    GPS proximity        ────────
    Duration             ───────
    Power discrepancy    ──────
    Speed correlation    ────────
    Wind confidence      ─────
    Sensor confidence    ───────

                        ↓

                 Review Priority

Importantly, the system should expose the evidence contributing to each flag.

## Example Output

    POSSIBLE DRAFTING SEGMENT

    Rider: #142
    Time: 31:42–32:17
    Duration: 35 s
    Course position: 23.7–24.2 km

    Nearest competitor:
    Rider #138

    Estimated separation:
    7–11 m

    Speed correlation:
    0.94

    Average power:
    238 W

    Expected solo power:
    327 W

    Confidence in telemetry:
    High

    Recommendation:
    Review race footage / official observations for this segment.

## Commissaire Dashboard

The interface could display:

    ┌──────────────────────────────┐
    │       COURSE MAP             │
    │                              │
    │      ───────⚠──────          │
    │                              │
    └──────────────────────────────┘

    Rider #142

    Power     ─────────╲____────
    Speed     ──────────────────
    Distance  ──────⚠───────────

Flags could be clicked to inspect the underlying telemetry.

## Potential Tech Stack

### Analysis

- Python
- Pandas
- NumPy
- SciPy

### FIT Processing

- fitparse
- Garmin FIT SDK

### Geospatial Analysis

- GeoPandas
- Shapely
- PostGIS

### Backend

- FastAPI

### Database

- PostgreSQL
- PostGIS

### Frontend

- React
- TypeScript
- Mapbox / Leaflet
- D3 / Recharts

## Development Roadmap

### Phase 1 — FIT Processing

- Upload FIT file
- Extract telemetry
- Clean GPS data
- Visualize route
- Display power/speed/elevation

### Phase 2 — Cycling Physics

- Calculate expected power
- Account for gradient
- Account for aerodynamic drag
- Compare expected vs measured power

### Phase 3 — Anomaly Detection

Identify periods where:

    observed power << expected solo power

while maintaining unusually high velocity.

### Phase 4 — Multi-Rider Analysis

- Upload entire race field
- Align timestamps
- Reconstruct rider positions
- Calculate rider-to-rider proximity
- Detect prolonged proximity

### Phase 5 — Evidence Scoring

Combine:

- Distance
- Duration
- Speed correlation
- Power discrepancy
- Course geometry
- Environmental uncertainty

into a review-priority model.

### Phase 6 — Commissaire Dashboard

Build:

- Interactive course map
- Rider timeline
- Flagged segments
- Telemetry graphs
- Rider comparisons
- Evidence explanations

### Phase 7 — Validation

Create controlled experiments using:

    Solo riding
    Legal passing
    Intentional drafting
    Vehicle following
    Tailwind riding
    GPS degradation

Measure:

- False-positive rate
- False-negative rate
- Detection precision
- Detection recall

## Important Design Principle

The software should never claim:

    "Rider X cheated."

Instead:

    "Telemetry between 31:42 and 32:17 contains characteristics consistent with possible drafting and warrants review."

Race penalties should remain decisions made by officials using all available evidence.

## Long-Term Ideas

- Real-time race monitoring
- Live GPS trackers
- Automated rider-pair detection
- Video synchronization
- Motorcycle/vehicle detection
- Weather-station integration
- Wind-field modeling
- Machine-learning classification
- Race-wide drafting heatmaps
- Automatic pass detection
- Team time-trial analysis
- Integration with timing systems

## Research Questions

This project could investigate:

1. How accurately can consumer GPS estimate rider separation?
2. Can power and speed data distinguish drafting from environmental effects?
3. How long must two rider trajectories overlap before drafting becomes statistically distinguishable from a legal pass?
4. Can multiple telemetry signals reduce false positives?
5. How reliably can drafting be detected without telemetry from the rider being followed?

## Project Status

**Status:** Foundational backbone implemented; working prototype in active development.

The core package establishes typed telemetry, rider, evidence, and analysis models plus
an auditable cycling-physics function. The implementation intentionally keeps screening
results separate from adjudication: every result is phrased as a review recommendation.

### Development setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,api,fit]'
pytest
```

## Working Prototype

The `feature/working-prototype` branch provides an end-to-end, local prototype. It accepts
multi-rider CSV files and individual FIT activities, validates telemetry, estimates solo
power demand, finds sustained synchronized GPS proximity, and produces explainable review
flags. It deliberately does not issue penalties or label a rider as having cheated.

A single FIT activity can be flagged from a sustained mismatch between measured power and
the estimated solo power required for its speed. Multi-rider uploads add independent GPS
proximity evidence and therefore support a higher-confidence assessment.

For activities with at least 20 usable power samples, RaceGuard infers a robust rider-specific
aerodynamic baseline from the file instead of assuming the default CdA. It adjusts each sample
for gradient and rolling resistance, then uses median absolute deviation to identify sustained
power-to-speed outliers. This keeps a consistently aerodynamic rider from being flagged simply
for having a better position while still surfacing same-power/higher-speed anomalies. Short
activities fall back to the configured physics profile and are reported with lower confidence.

Telemetry is evaluated with a centered seven-second rolling window. Windows around stopped
pedaling, soft pedaling, or the transition back onto power are excluded from power-anomaly
evidence using power and cadence when available. A genuine power-to-speed anomaly must remain
after smoothing and still satisfy the minimum segment duration; GPS proximity remains an
independent source of evidence in multi-rider files.

When an activity has elevation but no explicit grade—as is typical for FIT records—RaceGuard
derives gradient from the elevation change over at least 20 meters of traveled distance. The
power model also includes acceleration or deceleration measured across the rolling window.
Candidates must exceed both a 45 W absolute deficit and a 15% proportional deficit, which
prevents small sensor or model errors from being amplified merely because the rider is fast.

RaceGuard also performs within-activity section matching. Stable ten-second sections are
compared with non-adjacent earlier or later sections from the same rider when speed is within
0.75 m/s, gradient within 0.75 percentage points, and acceleration within 0.20 m/s². The
median power of at least two comparable sections becomes a rider-specific reference. A lower
power section must be a robust statistical outlier, exceed the absolute and proportional
thresholds, and persist long enough to become a review location. Repeated occurrences are
returned as separate chronological flags. This comparison is independent of assumed CdA.

### Analyze from the command line

The included sample contains two nearby riders and can be run without third-party packages:

```bash
PYTHONPATH=src python -m raceguard.cli examples/sample_race.csv
PYTHONPATH=src python -m raceguard.cli examples/sample_race.csv --json --output report.json
```

CSV columns are `rider_id`, ISO-8601 `timestamp`, `latitude`, `longitude`, and either
`speed_mps` or `speed_kph`. Optional columns are `power_w`, `elevation_m`, `cadence_rpm`,
`heart_rate_bpm`, `distance_m`, and decimal `gradient` (for example, `0.05` for 5%).

### Run the review console

```bash
python -m pip install -e '.[api]'
uvicorn raceguard.api:app --reload
```

Open `http://127.0.0.1:8000`, upload telemetry, and inspect the activity assessment,
confidence score, review locations, and contributing evidence. Up to 20 CSV/FIT activities
can be selected in one submission and are analyzed together. CSV files can contain one or
more riders; each FIT file receives an editable rider label defaulted from its filename.
Review locations are returned chronologically so an official can follow the activity timeline.
Uploaded files are processed locally in a temporary file and are deleted after analysis.
Interactive API documentation is available at `http://127.0.0.1:8000/docs`.

FIT input requires the optional dependency and a rider identifier:

```bash
python -m pip install -e '.[fit]'
raceguard activity.fit --rider-id 142
```

### Prototype limitations

- Multi-rider proximity currently requires samples with matching UTC timestamps.
- The physics model uses a constant configured wind value and does not infer wind direction.
- Segment coordinates and timestamps are included in the result contract so a future weather
  provider can supply local wind speed and direction without changing the upload workflow.
- Consumer GPS uncertainty can be similar to the distances under review.
- Scores are heuristic review priorities and require validation against controlled trials.
- Data is processed in memory; persistence, authentication, and race administration are not
  part of this local prototype.

Initial development should focus on FIT-file parsing, trajectory reconstruction, and detecting suspicious power-to-speed relationships.

## Motivation

Race officials cannot physically observe every rider throughout an individual time trial.

This project explores whether telemetry can act as an additional screening tool:

**Can GPS, power, speed, course, and environmental data automatically identify sections of an individual time trial that warrant human review for possible drafting?**
