"""Per-session workout detail — the drill-down behind the Workouts log.

A session row stores the API's own summary (duration, calories, avg HR). This module
adds what the summary can't show: the heart-rate trace *during* the session window
(from the intraday store) and time-in-zone computed from it.

Zone edges are %HRmax with the Fitbit-style bands (fat burn 50-69%, cardio 70-84%,
peak ≥85%). HRmax uses the app's cohort default (mid-20s → ~195 bpm); like the peer
benchmarks, it's directional context, not a lab test.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import store

HR_MAX = 195.0
CHART_POINTS = 320          # payload cap for the trace the UI charts
MAX_SAMPLE_GAP_S = 60.0     # a gap longer than this counts as "not measuring"


def _zone(hr: float) -> str:
    pct = hr / HR_MAX
    if pct >= 0.85:
        return "peak"
    if pct >= 0.70:
        return "cardio"
    if pct >= 0.50:
        return "fat_burn"
    return "light"


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def detail(workout_id: str) -> dict | None:
    w = store.get_workout(workout_id)
    if not w:
        return None

    start_ts = w["start_ts"]
    end_ts = w["end_ts"]
    if not end_ts and w.get("duration_min"):
        started = _parse_ts(start_ts)
        if started:
            end_ts = (started + timedelta(minutes=w["duration_min"])).isoformat() \
                .replace("+00:00", "Z")
    end_ts = end_ts or start_ts

    # Full-resolution samples for honest zone math; the chart gets a strided copy.
    samples = store.query_intraday_range("heart-rate", start_ts, end_ts, max_points=20000)
    samples = [s for s in samples if s["value"] is not None]

    zones = {"light": 0.0, "fat_burn": 0.0, "cardio": 0.0, "peak": 0.0}
    max_hr = None
    if samples:
        max_hr = max(s["value"] for s in samples)
        times = [_parse_ts(s["ts"]) for s in samples]
        for i, s in enumerate(samples):
            if times[i] is None:
                continue
            if i + 1 < len(samples) and times[i + 1] is not None:
                dt_s = min((times[i + 1] - times[i]).total_seconds(), MAX_SAMPLE_GAP_S)
            else:
                dt_s = 5.0  # last sample: one typical interval
            zones[_zone(s["value"])] += dt_s

    stride = max(1, -(-len(samples) // CHART_POINTS))
    trace = [{"ts": s["ts"], "value": s["value"]} for s in samples[::stride]]

    return {
        **w,
        "hr_trace": trace,
        "hr_max_session": round(max_hr, 0) if max_hr is not None else None,
        "hr_samples": len(samples),
        "zones_min": {k: round(v / 60, 1) for k, v in zones.items()},
        "hr_max_basis": f"zones use %HRmax with HRmax≈{HR_MAX:g} (mid-20s default)",
    }
