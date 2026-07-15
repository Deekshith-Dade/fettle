"""Daily readiness — a transparent recovery index computed from the stored daily metrics.

Mirrors Whoop/Oura-style logic (compare recent recovery signals against the user's own
rolling baseline) but the formula and constants are our own heuristic, not a medically
validated model. Five components, each 0-100, weighted and renormalized over whatever is
available for a given day:

  HRV     — 3-day average vs 28-day baseline (higher = more recovered)
  RHR     — 3-day average vs 28-day baseline (lower  = more recovered)
  Sleep   — duration (optimum ~8h) blended with efficiency
  Load    — prior-day active-zone-minutes vs baseline (heavier recent load = less fresh)
  Temp    — skin-temperature deviation from baseline (elevation = strain/illness signal)

The per-day scores are stored under the "readiness" data type so they chart like any other
metric; `today_breakdown()` returns the latest day's components for the dashboard hero.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from . import store

WEIGHTS = {"hrv": 0.30, "rhr": 0.20, "sleep": 0.30, "load": 0.12, "temp": 0.08}
_RECENT_DAYS = 3      # smoothing window for HRV/RHR
_BASELINE_DAYS = 28   # personal baseline window


def _series(data_type: str) -> dict[date, float]:
    return {
        date.fromisoformat(r["day"]): r["value"]
        for r in store.query_daily(data_type, None, None)
        if r["value"] is not None
    }


def _mean_in(series: dict[date, float], lo: date, hi: date) -> float | None:
    vals = [v for d, v in series.items() if lo <= d <= hi]
    return sum(vals) / len(vals) if vals else None


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _components_for(
    d: date,
    hrv: dict[date, float], rhr: dict[date, float],
    dur: dict[date, float], eff: dict[date, float],
    load: dict[date, float], load_unit: str, temp: dict[date, float],
) -> list[dict[str, Any]]:
    """The available recovery components for day `d`, each a 0-100 sub-score."""
    comps: list[dict[str, Any]] = []
    recent = lambda s: _mean_in(s, d - timedelta(days=_RECENT_DAYS - 1), d)  # noqa: E731
    base = lambda s: _mean_in(s, d - timedelta(days=_BASELINE_DAYS + 2), d - timedelta(days=_RECENT_DAYS))  # noqa: E731

    r, b = recent(hrv), base(hrv)
    if r is not None and b:
        comps.append({"key": "hrv", "label": "HRV", "score": _clamp(75 + (r / b - 1) * 125, 0, 100),
                      "value": round(r, 1), "unit": "ms", "delta": round(r - b, 1), "good": r >= b})

    r, b = recent(rhr), base(rhr)
    if r is not None and b:
        comps.append({"key": "rhr", "label": "Resting HR", "score": _clamp(75 + (b / r - 1) * 150, 0, 100),
                      "value": round(r), "unit": "bpm", "delta": round(r - b, 1), "good": r <= b})

    du, ef = dur.get(d), eff.get(d)
    if du:  # a 0h duration is always a sync artifact, never a real night — treat as missing
        dscore = _clamp(100 - abs(du - 8) * 18, 0, 100)
        escore = _clamp(ef, 0, 100) if ef is not None else dscore
        comps.append({"key": "sleep", "label": "Sleep", "score": 0.6 * dscore + 0.4 * escore,
                      "value": round(du, 1), "unit": "h", "delta": None, "good": True})

    lr, lb = load.get(d - timedelta(days=1)), _mean_in(load, d - timedelta(days=_BASELINE_DAYS), d - timedelta(days=1))
    if lr is not None and lb:
        comps.append({"key": "load", "label": "Training Load", "score": _clamp(88 - (lr / lb - 1) * 45, 30, 100),
                      "value": round(lr), "unit": load_unit, "delta": round(lr - lb), "good": lr <= lb})

    # The stored skin-temp series is already the nightly deviation from personal baseline.
    dev = temp.get(d)
    if dev is not None:
        comps.append({"key": "temp", "label": "Skin Temp", "score": _clamp(92 - abs(dev) * 35, 0, 100),
                      "value": round(dev, 2), "unit": "°C dev", "delta": None, "good": abs(dev) < 0.3})

    return comps


def _score(comps: list[dict[str, Any]]) -> int | None:
    if not comps:
        return None
    wsum = sum(WEIGHTS[c["key"]] for c in comps)
    acc = sum(c["score"] * WEIGHTS[c["key"]] for c in comps)
    return round(acc / wsum)


def _tone(score: int) -> str:
    return "primed" if score >= 82 else "ready" if score >= 66 else "steady" if score >= 50 else "recovering"


def _narrative(comps: list[dict[str, Any]]) -> str:
    by = {c["key"]: c for c in comps}
    bits: list[str] = []
    if "hrv" in by:
        bits.append("HRV is above your baseline" if by["hrv"]["score"] >= 70 else "HRV is running below baseline")
    if "sleep" in by:
        bits.append("sleep held up well" if by["sleep"]["score"] >= 70 else "sleep was on the light side")
    if "load" in by and by["load"]["score"] < 55:
        bits.append("yesterday's training load was heavy")
    if "temp" in by and by["temp"]["score"] < 60:
        bits.append("skin temperature is elevated")
    if "rhr" in by and by["rhr"]["score"] < 55:
        bits.append("resting heart rate is up")
    text = ", ".join(bits)
    return (text[0].upper() + text[1:] + ".") if text else "Not enough signal yet."


def _all_series() -> tuple:
    """(hrv, rhr, duration, efficiency, load, load_unit, temp). Training load prefers the
    TRIMP-style cardio-load series and falls back to raw active-zone-minutes."""
    load, load_unit = _series("cardio-load"), "load"
    if not load:
        load, load_unit = _series("active-zone-minutes"), "min"
    return (
        _series("daily-heart-rate-variability"), _series("daily-resting-heart-rate"),
        _series("sleep-duration"), _series("sleep-efficiency"),
        load, load_unit, _series("daily-sleep-temperature-derivations"),
    )


def recompute() -> int:
    """Recompute readiness for every day we can score and store it. Returns row count."""
    hrv, rhr, dur, eff, load, load_unit, temp = _all_series()
    days = sorted(set(hrv) | set(rhr) | set(dur))
    scores: dict[str, float] = {}
    for d in days:
        comps = _components_for(d, hrv, rhr, dur, eff, load, load_unit, temp)
        if len(comps) >= 2:  # avoid scoring off a single lonely signal
            sc = _score(comps)
            if sc is not None:
                scores[d.isoformat()] = sc
    return store.upsert_daily_values("readiness", "", scores)


def today_breakdown() -> dict[str, Any] | None:
    """Latest scorable day's score + component breakdown for the dashboard hero."""
    hrv, rhr, dur, eff, load, load_unit, temp = _all_series()
    days = sorted(set(hrv) | set(rhr) | set(dur))
    for d in reversed(days):
        comps = _components_for(d, hrv, rhr, dur, eff, load, load_unit, temp)
        if len(comps) >= 2:
            score = _score(comps)
            if score is not None:
                for c in comps:
                    c["score"] = round(c["score"])
                return {"date": d.isoformat(), "score": score, "tone": _tone(score),
                        "narrative": _narrative(comps), "components": comps}
    return None
