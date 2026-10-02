"""FastAPI adapter for the working prototype."""

from __future__ import annotations

import tempfile
from importlib.resources import files
from pathlib import Path

try:
    from fastapi import FastAPI, File, HTTPException, UploadFile
    from fastapi.responses import HTMLResponse
except ImportError as exc:  # pragma: no cover - depends on optional installation
    raise RuntimeError("API support requires: pip install -e '.[api]'") from exc

from .analysis import analyze
from .ingest import TelemetryError, load_csv, load_fit
from .reporting import result_to_dict

app = FastAPI(
    title="RaceGuard",
    version="0.1.0",
    description="Review-oriented telemetry screening; results are not adjudications.",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return files("raceguard").joinpath("static/index.html").read_text(encoding="utf-8")


@app.post("/api/analyze")
async def analyze_upload(file: UploadFile = File(...), rider_id: str | None = None) -> dict:
    suffix = Path(file.filename or "telemetry.csv").suffix.lower()
    if suffix not in {".csv", ".fit"}:
        raise HTTPException(400, "Upload a .csv or .fit file")
    content = await file.read()
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(413, "File exceeds the 25 MB prototype limit")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
            temporary.write(content)
            temporary_path = Path(temporary.name)
        if suffix == ".fit":
            if not rider_id:
                raise TelemetryError("rider_id is required for FIT input")
            points = load_fit(temporary_path, rider_id)
        else:
            points = load_csv(temporary_path)
        return result_to_dict(analyze(points))
    except TelemetryError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

