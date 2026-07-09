"""Peer benchmarks — where you stand vs. established reference norms.

Readiness/insights read *your* history against *itself*. This module does the other axis:
it places your habitual value for each key metric on an evidence-based reference scale for a
healthy adult (defaults tuned to the profile — a man in his mid-20s), names the tier you're
in, and points at the next reachable rung. The intent is orientation and a gentle target to
push toward, not a medical assessment.

Each benchmark carries its `basis` (the source of the bands) so nothing here is a magic
number. HRV in particular is far more meaningful against your own baseline than against a
population — the peer context is directional only, and we say so.

Reference bases:
  - Resting HR bands: standard cardiorespiratory-fitness resting-HR tables (age 18–25 male);
    AHA normal adult range is 60–100 bpm, fitness bands sit lower.
  - HRV (Fitbit reports nightly RMSSD): population RMSSD for 20–34 sits ~broadly, higher is
    generally better; best read vs. your own baseline.
  - Steps: Tudor-Locke & Bassett (2004) pedometer activity classification.
  - Sleep duration: National Sleep Foundation — 7–9 h for adults 18–25.
  - Sleep efficiency: sleep-medicine convention — ≥85 % normal, ≥90 % excellent.
  - Respiratory rate: normal resting adult 12–20 breaths/min.
  - SpO2: ≥95 % normal at sea level.
  - Weekly active minutes: WHO 2020 — 150–300 min moderate-to-vigorous / week.
  - BMI: WHO classification (healthy 18.5–24.9).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from . import store

# Local calendar "today" for excluding a still-accruing day from cumulative means.
def _today_iso() -> str:
    return date.today().isoformat()


@dataclass(frozen=True)
class Band:
    name: str
    lo: float
    hi: float
    tone: str  # laddered: under|typical|good|optimal · range: low|healthy|high


@dataclass(frozen=True)
class Benchmark:
    key: str                 # data_type (or synthetic key for derived ones)
    label: str
    unit: str
    better: str              # "up" | "down" | "range"
    scale: tuple[float, float]
    bands: tuple[Band, ...]  # ascending by value, spanning the scale
    basis: str
    caveat: str = ""
    mode: str = "mean"       # "mean" | "weekly_sum" | "bmi"
    window: int = 28
    cumulative: bool = False  # drop a partial "today" before averaging


# Tones that read as "you're doing well here" — used to pick the next target rung.
_GOOD_TONES = {"good", "optimal", "healthy"}


BENCHMARKS: tuple[Benchmark, ...] = (
    Benchmark(
        key="daily-resting-heart-rate", label="Resting heart rate", unit="bpm",
        better="down", scale=(45, 90),
        bands=(
            Band("athletic", 45, 57, "optimal"),
            Band("excellent", 57, 66, "good"),
            Band("average", 66, 75, "typical"),
            Band("raised", 75, 90, "under"),
        ),
        basis="Cardiorespiratory-fitness resting-HR tables for men 18–25. "
              "A lower resting HR generally reflects a stronger, more efficient heart.",
    ),
    Benchmark(
        key="daily-heart-rate-variability", label="Heart-rate variability", unit="ms",
        better="up", scale=(20, 90),
        bands=(
            Band("low", 20, 35, "under"),
            Band("typical", 35, 50, "typical"),
            Band("strong", 50, 70, "good"),
            Band("elite", 70, 90, "optimal"),
        ),
        basis="Nightly RMSSD ranges for healthy adults in their 20s. Higher HRV tracks better "
              "recovery and autonomic balance.",
        caveat="HRV is highly individual — your own baseline matters far more than this scale. "
               "Read peer position as a rough compass, not a verdict.",
    ),
    Benchmark(
        key="steps", label="Daily steps", unit="steps",
        better="up", scale=(0, 14000), cumulative=True,
        bands=(
            Band("sedentary", 0, 5000, "under"),
            Band("low active", 5000, 7500, "typical"),
            Band("active", 7500, 10000, "good"),
            Band("highly active", 10000, 14000, "optimal"),
        ),
        basis="Tudor-Locke & Bassett (2004) step-count activity classification.",
    ),
    Benchmark(
        key="sleep-duration", label="Sleep duration", unit="h",
        better="up", scale=(4.5, 9.5),
        bands=(
            Band("short", 4.5, 6, "under"),
            Band("slightly short", 6, 7, "typical"),
            Band("recommended", 7, 9, "optimal"),
            Band("long", 9, 9.5, "typical"),
        ),
        basis="National Sleep Foundation — adults 18–25 need 7–9 h/night.",
    ),
    Benchmark(
        key="sleep-efficiency", label="Sleep efficiency", unit="%",
        better="up", scale=(72, 100),
        bands=(
            Band("low", 72, 80, "under"),
            Band("fair", 80, 85, "typical"),
            Band("good", 85, 90, "good"),
            Band("excellent", 90, 100, "optimal"),
        ),
        basis="Sleep-medicine convention — ≥85 % of time in bed asleep is normal, ≥90 % excellent.",
    ),
    Benchmark(
        key="weekly-active-minutes", label="Active minutes / week", unit="min",
        better="up", scale=(0, 400), mode="weekly_sum",
        bands=(
            Band("below guideline", 0, 150, "under"),
            Band("meets guideline", 150, 300, "good"),
            Band("exceeds", 300, 400, "optimal"),
        ),
        basis="WHO 2020 — 150–300 min of moderate-to-vigorous activity per week.",
        caveat="Approximated from Active Zone Minutes (elevated-HR time).",
    ),
    Benchmark(
        key="daily-respiratory-rate", label="Breathing rate", unit="brpm",
        better="range", scale=(10, 22),
        bands=(
            Band("low", 10, 12, "low"),
            Band("healthy", 12, 20, "healthy"),
            Band("high", 20, 22, "high"),
        ),
        basis="Normal resting adult respiratory rate is 12–20 breaths/min.",
    ),
    Benchmark(
        key="daily-oxygen-saturation", label="Blood oxygen (SpO2)", unit="%",
        better="range", scale=(90, 100),
        bands=(
            Band("low", 90, 94, "low"),
            Band("borderline", 94, 95, "high"),
            Band("normal", 95, 100, "healthy"),
        ),
        basis="Resting SpO2 ≥95 % is normal at sea level.",
    ),
    Benchmark(
        key="bmi", label="Body-mass index", unit="",
        better="range", scale=(16, 32), mode="bmi",
        bands=(
            Band("underweight", 16, 18.5, "low"),
            Band("healthy", 18.5, 25, "healthy"),
            Band("overweight", 25, 30, "high"),
            Band("obese", 30, 32, "high"),
        ),
        basis="WHO body-mass-index classification.",
        caveat="BMI ignores muscle mass and build — context, not a diagnosis.",
    ),
)


# --- computing the user's habitual value -------------------------------------

def _vals(cache: dict[str, list[dict]], key: str) -> list[tuple[str, float]]:
    return [(r["day"], float(r["value"]))
            for r in cache.get(key, []) if r.get("value") is not None]


def _habitual(cache: dict[str, list[dict]], b: Benchmark) -> float | None:
    """The single number we place on the scale, per the benchmark's mode."""
    if b.mode == "weekly_sum":
        # Average the last 4 completed weeks of Active Zone Minutes → a representative week.
        rows = _vals(cache, "active-zone-minutes")
        if len(rows) < 7:
            return None
        vals = [v for _, v in rows][-28:]
        return round(sum(vals) / len(vals) * 7, 0)
    if b.mode == "bmi":
        w = _vals(cache, "weight")
        h = _vals(cache, "height")
        if not w or not h:
            return None
        kg, cm = w[-1][1], h[-1][1]
        if cm <= 0:
            return None
        return round(kg / (cm / 100) ** 2, 1)
    rows = _vals(cache, b.key)
    if b.cumulative and rows and rows[-1][0] == _today_iso():
        rows = rows[:-1]  # drop a still-accruing day
    vals = [v for _, v in rows][-b.window:]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 1)


def _band_for(b: Benchmark, value: float) -> Band:
    for band in b.bands:
        if band.lo <= value < band.hi:
            return band
    # Clamp to the nearest edge band for out-of-scale values.
    return b.bands[0] if value < b.bands[0].lo else b.bands[-1]


def _position(b: Benchmark, value: float) -> float:
    lo, hi = b.scale
    return round(max(0.0, min(100.0, (value - lo) / (hi - lo) * 100)), 1)


def _next_target(b: Benchmark, value: float, current: Band) -> dict | None:
    """The next rung to aim for, in the improving direction — or None if already there."""
    idx = b.bands.index(current)
    if b.better == "up":
        if current.tone in {"optimal"}:
            return None
        nxt = b.bands[idx + 1] if idx + 1 < len(b.bands) else None
        if nxt:
            return {"label": nxt.name, "value": round(nxt.lo, 1), "comparator": "gte"}
    elif b.better == "down":
        if current.tone in {"optimal"}:
            return None
        prev = b.bands[idx - 1] if idx - 1 >= 0 else None
        if prev:
            return {"label": prev.name, "value": round(current.lo, 1), "comparator": "lte"}
    else:  # range — only nudge if outside the healthy band
        if current.tone == "healthy":
            return None
        healthy = next((bd for bd in b.bands if bd.tone == "healthy"), None)
        if healthy:
            if value < healthy.lo:
                return {"label": healthy.name, "value": round(healthy.lo, 1), "comparator": "gte"}
            return {"label": healthy.name, "value": round(healthy.hi, 1), "comparator": "lte"}
    return None


def evaluate_all(cohort: str = "Adult male · mid-20s") -> dict:
    """Every benchmark with enough data, scored against its reference bands."""
    cache = store.query_daily_bulk()
    out: list[dict] = []
    latest_day = None
    for b in BENCHMARKS:
        value = _habitual(cache, b)
        if value is None:
            continue
        band = _band_for(b, value)
        rows = _vals(cache, b.key if b.mode == "mean" else "active-zone-minutes")
        if rows:
            latest_day = max(latest_day or rows[-1][0], rows[-1][0])
        out.append({
            "key": b.key,
            "label": b.label,
            "unit": b.unit,
            "better": b.better,
            "value": value,
            "tier": band.name,
            "tone": band.tone,
            "scale": list(b.scale),
            "position": _position(b, value),
            "bands": [
                {"name": bd.name, "lo": bd.lo, "hi": bd.hi, "tone": bd.tone,
                 "start": _position(b, bd.lo), "end": _position(b, bd.hi)}
                for bd in b.bands
            ],
            "target": _next_target(b, value, band),
            "basis": b.basis,
            "caveat": b.caveat,
        })
    # Order: things to work on first (under/low), then typical, then wins — a gentle arc
    # that opens on what matters and closes on encouragement.
    tone_rank = {"under": 0, "low": 1, "high": 1, "typical": 2, "good": 3,
                 "healthy": 3, "optimal": 4}
    out.sort(key=lambda x: tone_rank.get(x["tone"], 2))
    return {"as_of": latest_day, "cohort": cohort, "benchmarks": out}
