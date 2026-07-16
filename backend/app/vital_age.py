"""Vital Age — how old your body *behaves*, from the metrics fettle already tracks.

The same idea WHOOP Age and Bevel sell: compare your habitual health markers to what's
typical for each age, and report the age your profile most resembles. Younger than your
birthday is good. Unlike those products this is fully transparent — every curve below is
an inversion of a *published age-norm*, and each component hands back its own basis, its
equivalent age, and how many years above/below your real age it lands.

Two tiers, mirroring how WHOOP splits physiology from behaviour:

  Physiological core — invert a population age→value curve to read an "equivalent age":
    • HRV (nightly RMSSD): parasympathetic tone falls ~2.4%/yr through mid-life.
    • Cardiorespiratory fitness: we can't measure VO₂max (no data from the watch), so we
      ESTIMATE it from resting HR via the Heart-Rate-Ratio method, then place that on the
      VO₂max-by-age curve. This folds resting HR in — so RHR is deliberately NOT a
      separate component (it would double-count the same cardiovascular trait; WHOOP uses
      SEM to avoid exactly this).

  Behavioural adjustment — bounded year offsets from evidence targets (not tight age
  curves, so we don't pretend a step count maps to a birthday): sleep and daily activity.

Vital Age = chronological_age + Σ wᵢ·Δᵢ, where Δᵢ is each component's years-older-than-you.
If every marker sits exactly at your-age norm, every Δ is 0 and Vital Age == your age.

References (median/50th-percentile curves, male unless noted):
  • RMSSD by age: pooled healthy-adult RMSSD norms (20-29 ≈ 55 ms falling to ~21 ms by 65);
    vagal HRV declines ~1-3%/yr after the mid-20s. (Umetani 1998; Nunan 2010 pooled norms.)
  • HRmax = 211 − 0.64·age — the HUNT Fitness Study (Nes 2013), better calibrated than 220−age.
  • VO₂max = 15.3·(HRmax/HRrest) — Heart-Rate-Ratio method (Uth 2004); ±15% typical error.
  • VO₂max-by-age median: Cooper Institute / ACSM Guidelines 11th ed. men (~48 @25 → ~35 @55),
    fit here as 57.8 − 0.445·age over the 20-60 range.
  • Steps: Tudor-Locke activity bands; all-cause-mortality benefit rises to ~7-8k/day.
  • Activity minutes: WHO 2020 — 150-300 min moderate-vigorous/week.
  • Sleep: National Sleep Foundation 7-9 h for adults.
This is orientation and motivation, not a medical or a validated biological-age clock.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from . import store
from .config import settings

# --- profile -------------------------------------------------------------------

def chronological_age(on: date | None = None) -> float:
    """Exact age in years from the configured birth date (fractional — a Vital Age of
    'your age + 0.2' shouldn't round-trip to a whole-year lie)."""
    on = on or date.today()
    b = date.fromisoformat(settings.birth_date)
    return (on - b).days / 365.2425


# --- age-norm curves (median value for a given age) -----------------------------

def _rmssd_median(age: float) -> float:
    """Median nightly RMSSD (ms) at a given age: 100.4·e^(−0.0241·age).
    Fit to pooled healthy-adult norms (≈55 ms @25, ≈34 ms @45, ≈21 ms @65)."""
    return 100.4 * math.exp(-0.0241 * age)


def _rmssd_equiv_age(rmssd: float) -> float:
    """Invert the RMSSD curve: the age at which this RMSSD is typical."""
    return math.log(100.4 / rmssd) / 0.0241


def _vo2_from_rhr(rhr: float, age: float) -> float:
    """Estimated VO₂max (ml/kg/min) via the Heart-Rate-Ratio method."""
    hr_max = 211.0 - 0.64 * age
    return 15.3 * (hr_max / rhr)


def _vo2_median(age: float) -> float:
    """Median male VO₂max (ml/kg/min) at a given age (Cooper/ACSM), linear 20-60."""
    return 57.8 - 0.445 * age


def _vo2_equiv_age(vo2: float) -> float:
    return (57.8 - vo2) / 0.445


# --- behavioural year-offset maps ----------------------------------------------

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _sleep_offset(score: float | None, dur: float | None) -> float | None:
    """Years-older from sleep habit. Anchored at a 'good' night (score 85, ~7.75 h);
    each 10 score-points ≈ 1.5 yr, bounded ±4. Chronic short sleep adds up to 2 yr more."""
    if score is None and dur is None:
        return None
    off = 0.0
    if score is not None:
        off += (85.0 - score) / 10.0 * 1.5
    if dur is not None and dur < 7.0:
        off += (7.0 - dur) * 2.0  # below the NSF floor is an independent penalty
    return _clamp(off, -4.0, 5.0)


def _activity_offset(steps: float | None, azm_week: float | None) -> float | None:
    """Years-older from daily movement. Steps anchored at the ~7.5k mortality-benefit
    knee; AZM at the WHO 150 min/week floor (300 = full credit). Bounded ±4."""
    if steps is None and azm_week is None:
        return None
    off = 0.0
    if steps is not None:
        off += _clamp((7500.0 - steps) / 2500.0, -2.0, 2.5)  # ±1 per 2.5k steps
    if azm_week is not None:
        # 300+ min/wk → −2 (younger); 150 → 0; 0 → +2.
        off += _clamp((150.0 - azm_week) / 75.0, -2.0, 2.5)
    return _clamp(off, -4.0, 5.0)


# --- component assembly ---------------------------------------------------------

@dataclass
class Component:
    key: str
    label: str
    weight: float
    delta: float                 # years older(+)/younger(−) than chronological
    detail: str
    basis: str
    equiv_age: float | None = None
    value: float | None = None
    unit: str = ""

    def as_dict(self, chrono: float) -> dict:
        return {
            "key": self.key, "label": self.label, "weight": round(self.weight, 2),
            "delta": round(self.delta, 1),
            "equiv_age": round(self.equiv_age, 1) if self.equiv_age is not None else None,
            "value": round(self.value, 1) if self.value is not None else None,
            "unit": self.unit, "detail": self.detail, "basis": self.basis,
        }


def _mean(series: list, days: int) -> float | None:
    vals = [v for _, v in series][-days:]
    return sum(vals) / len(vals) if vals else None


def _series(cache: dict, data_type: str) -> list[tuple[str, float]]:
    rows = cache.get(data_type, [])
    return [(r["day"], r["value"]) for r in rows if r.get("value") is not None]


# The physiological core carries most of the weight; behaviour trims a few years.
WEIGHTS = {"hrv": 0.38, "fitness": 0.32, "sleep": 0.15, "activity": 0.15}
WINDOW = 28          # habitual = last 4 weeks
MIN_NIGHTS = 10      # below this the physiological read is too thin to trust
DEV_CAP = 12.0       # never claim more than ±12 yr from chronological


def compute(today: date | None = None) -> dict | None:
    """The Vital Age payload, or None if there isn't enough data to stand one up."""
    today = today or date.today()
    chrono = chronological_age(today)
    cache = store.query_daily_bulk()

    hrv = _mean(_series(cache, "daily-heart-rate-variability"), WINDOW)
    rhr = _mean(_series(cache, "daily-resting-heart-rate"), WINDOW)
    slp_score = _mean(_series(cache, "sleep-score"), WINDOW)
    slp_dur = _mean(_series(cache, "sleep-duration"), WINDOW)
    steps = _mean(_series(cache, "steps"), WINDOW)
    azm = _mean(_series(cache, "active-zone-minutes"), WINDOW)
    azm_week = azm * 7 if azm is not None else None
    nights = len([1 for _, v in _series(cache, "daily-heart-rate-variability")][-WINDOW:])

    comps: list[Component] = []

    if hrv is not None:
        ea = _rmssd_equiv_age(hrv)
        comps.append(Component(
            "hrv", "Heart-rate variability", WEIGHTS["hrv"], ea - chrono,
            equiv_age=ea, value=hrv, unit="ms",
            detail=f"Your {hrv:.0f} ms nightly RMSSD is typical of a {ea:.0f}-year-old.",
            basis="RMSSD-by-age norm (declines ~2.4%/yr); higher is younger."))

    if rhr is not None:
        vo2 = _vo2_from_rhr(rhr, chrono)
        ea = _clamp(_vo2_equiv_age(vo2), 15.0, 90.0)
        comps.append(Component(
            "fitness", "Cardio fitness", WEIGHTS["fitness"], ea - chrono,
            equiv_age=ea, value=vo2, unit="ml/kg/min (est.)",
            detail=(f"Estimated VO₂max {vo2:.0f} from your {rhr:.0f} bpm resting HR — "
                    f"typical of a {ea:.0f}-year-old man."),
            basis="VO₂max est. from resting HR (Heart-Rate-Ratio, ±15%) vs ACSM age norms."))

    so = _sleep_offset(slp_score, slp_dur)
    if so is not None:
        comps.append(Component(
            "sleep", "Sleep", WEIGHTS["sleep"], so, value=slp_score, unit="score",
            detail=(f"Sleep score {slp_score:.0f}"
                    + (f", {slp_dur:.1f} h/night" if slp_dur is not None else "")
                    + " vs a restorative ~85 / 7.75 h."),
            basis="Offset from a good-night anchor; National Sleep Foundation 7-9 h."))

    ao = _activity_offset(steps, azm_week)
    if ao is not None:
        comps.append(Component(
            "activity", "Daily activity", WEIGHTS["activity"], ao, value=steps, unit="steps/day",
            detail=(f"{steps:.0f} steps/day"
                    + (f", {azm_week:.0f} active-zone min/week" if azm_week is not None else "")
                    + " vs 7.5k steps & WHO 150-300 min/week."),
            basis="Offset from Tudor-Locke step bands & WHO activity floor."))

    physiological = [c for c in comps if c.key in ("hrv", "fitness")]
    if not physiological or nights < MIN_NIGHTS:
        return None  # a Vital Age with no cardiovascular read isn't worth showing

    wsum = sum(c.weight for c in comps)
    dev = _clamp(sum(c.weight * c.delta for c in comps) / wsum, -DEV_CAP, DEV_CAP)
    vital = chrono + dev

    # Confidence scales with how many of the four components we could actually fill,
    # and whether the fitness read rests on a measured vs estimated VO₂max (always est. here).
    coverage = len(comps) / 4.0
    confidence = "high" if coverage >= 1.0 and nights >= 21 else \
                 "moderate" if coverage >= 0.75 and nights >= MIN_NIGHTS else "low"

    driver = max(comps, key=lambda c: c.weight * abs(c.delta))
    lead = "younger" if dev < -0.4 else "older" if dev > 0.4 else "on par"

    return {
        "as_of": today.isoformat(),
        "chronological_age": round(chrono, 1),
        "vital_age": round(vital, 1),
        "delta_years": round(dev, 1),
        "verdict": lead,
        "confidence": confidence,
        "nights": nights,
        "driver": driver.key,
        "headline": _headline(vital, chrono, dev, driver),
        "components": [c.as_dict(chrono) for c in comps],
        "method": ("Vital Age inverts published age-norm curves for HRV and estimated "
                   "cardio fitness, then nudges by sleep and activity habits. Transparent "
                   "heuristic for motivation — not a medical biological-age test."),
    }


def _headline(vital: float, chrono: float, dev: float, driver: "Component") -> str:
    if abs(dev) < 0.4:
        return f"Your body reads right on your age — about {vital:.0f}."
    dirn = "younger" if dev < 0 else "older"
    years = abs(dev)
    unit = "year" if 0.5 <= years < 1.5 else "years"
    tail = (f" Your strongest lever is {driver.label.lower()}."
            if dev > 0 else f" {driver.label} is carrying it.")
    return f"Your body behaves about {years:.0f} {unit} {dirn} than {chrono:.0f}.{tail}"
