"""Insights — the interpretation layer.

Readiness and the sleep/cardio scores turn raw points into *metrics*; this module turns
the metrics into *observations*: the plain-English things a good coach would notice for
you. It is stateless and computed on request (like readiness.today_breakdown), reading the
stored daily series and emitting a ranked list of insights.

Seven detectors, each a pure function over the daily series:

  trend        — a metric drifting up or down beyond its own noise (least-squares slope
                 with an R² gate), read in the direction that matters for that metric.
  anomaly      — the latest reading sitting far from the personal baseline (z-score).
  record       — a fresh all-time high/low set on the most recent day.
  streak       — consecutive recent days clearing a goal (sleep, readiness, steps…).
  load         — acute:chronic workload ratio (ACWR) from cardio-load: the sports-science
                 balance of recent vs habitual training, with the injury-risk zones.
  sleep_debt   — cumulative shortfall against an 8h target over the last week.
  correlation  — how strongly one metric tracks next-day readiness (Pearson r).

Every insight is a dict {id, kind, sentiment, title, detail, metric?, priority}; the
endpoint returns them sorted by priority so the most actionable rises to the top.
Sentiment ∈ {good, watch, bad, info} drives the dashboard's color coding.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Callable

from . import store

# Direction that counts as an improvement, per metric. "down" means lower-is-better.
GOOD_DIR: dict[str, str] = {
    "daily-resting-heart-rate": "down",
    "daily-heart-rate-variability": "up",
    "daily-respiratory-rate": "down",
    "daily-oxygen-saturation": "up",
    "sleep-score": "up",
    "sleep-duration": "up",
    "sleep-efficiency": "up",
    "sleep-deep": "up",
    "sleep-rem": "up",
    "readiness": "up",
    "steps": "up",
    "cardio-load": "neutral",
    "daily-sleep-temperature-derivations": "neutral",
    "weight": "neutral",
}

LABELS: dict[str, str] = {
    "daily-resting-heart-rate": "Resting heart rate",
    "daily-heart-rate-variability": "HRV",
    "daily-respiratory-rate": "Breathing rate",
    "daily-oxygen-saturation": "Blood oxygen",
    "sleep-score": "Sleep score",
    "sleep-duration": "Sleep duration",
    "sleep-efficiency": "Sleep efficiency",
    "sleep-deep": "Deep sleep",
    "sleep-rem": "REM sleep",
    "readiness": "Readiness",
    "steps": "Steps",
    "cardio-load": "Cardio load",
    "daily-sleep-temperature-derivations": "Skin temperature",
    "weight": "Weight",
}

UNITS: dict[str, str] = {
    "daily-resting-heart-rate": "bpm", "daily-heart-rate-variability": "ms",
    "daily-respiratory-rate": "brpm", "daily-oxygen-saturation": "%",
    "sleep-score": "", "sleep-duration": "h", "sleep-efficiency": "%",
    "sleep-deep": "h", "sleep-rem": "h", "readiness": "", "steps": "steps",
    "cardio-load": "", "daily-sleep-temperature-derivations": "°C", "weight": "kg",
}


# --- small stats toolkit -------------------------------------------------------

def _series(data_type: str, cache: dict[str, list[dict]]) -> list[tuple[date, float]]:
    """(date, value) pairs for a type, ascending, nulls dropped."""
    rows = cache.get(data_type) or []
    out = [
        (date.fromisoformat(r["day"]), float(r["value"]))
        for r in rows if r.get("value") is not None
    ]
    out.sort()
    return out


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _std(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _linfit(ys: list[float]) -> tuple[float, float]:
    """Least-squares slope (per step) and R² for y over its integer index."""
    n = len(ys)
    if n < 3:
        return 0.0, 0.0
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    if sxx == 0:
        return 0.0, 0.0
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((ys[i] - (intercept + slope * xs[i])) ** 2 for i in range(n))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return slope, r2


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 4:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / (sx * sy)


def _fmt(v: float, unit: str = "") -> str:
    if abs(v) >= 100:
        s = f"{v:,.0f}"
    elif abs(v) >= 10:
        s = f"{v:.0f}" if abs(v - round(v)) < 0.05 else f"{v:.1f}"
    else:
        s = f"{v:.1f}"
    return f"{s} {unit}".strip() if unit else s


# --- detectors -----------------------------------------------------------------

def _trends(cache: dict[str, list[dict]]) -> list[dict[str, Any]]:
    """Metrics drifting meaningfully over the last ~14 days."""
    out = []
    win = 14
    for dt, gdir in GOOD_DIR.items():
        s = _series(dt, cache)
        if len(s) < 8:
            continue
        recent = s[-win:]
        ys = [v for _, v in recent]
        slope, r2 = _linfit(ys)
        change = slope * (len(ys) - 1)              # total drift across the window
        base = _mean(ys) or 0.0
        rel = abs(change) / abs(base) if base else 0.0
        # Needs a coherent direction (R²) and a change worth mentioning (either a few %
        # of the level, or — for scores — a few absolute points).
        pts_metric = dt in ("readiness", "sleep-score")
        big = rel >= 0.06 or (pts_metric and abs(change) >= 6)
        if r2 < 0.35 or not big:
            continue
        rising = change > 0
        if gdir == "neutral":
            sentiment = "info"
        else:
            good = (rising and gdir == "up") or (not rising and gdir == "down")
            sentiment = "good" if good else "watch"
        arrow = "climbing" if rising else "easing"
        label = LABELS.get(dt, dt)
        unit = UNITS.get(dt, "")
        out.append({
            "id": f"trend-{dt}", "kind": "trend", "sentiment": sentiment, "metric": dt,
            "title": f"{label} is {arrow}",
            "detail": f"{'Up' if rising else 'Down'} about {_fmt(abs(change), unit)} "
                      f"over the last {len(ys)} days ({label.lower()} now "
                      f"{_fmt(ys[-1], unit)}).",
            "priority": 40 + rel * 100 * (0.6 if sentiment == "good" else 1.0) + r2 * 20,
        })
    return out


def _anomalies(cache: dict[str, list[dict]]) -> list[dict[str, Any]]:
    """Latest reading far from the 28-day personal baseline."""
    out = []
    for dt, gdir in GOOD_DIR.items():
        s = _series(dt, cache)
        if len(s) < 10:
            continue
        last_day, last_v = s[-1]
        hist = [v for _, v in s[:-1]][-28:]
        m, sd = _mean(hist), _std(hist)
        if m is None or sd < 1e-9:
            continue
        z = (last_v - m) / sd
        if abs(z) < 2.0:
            continue
        higher = z > 0
        if gdir == "neutral":
            sentiment = "info"
        else:
            good = (higher and gdir == "up") or (not higher and gdir == "down")
            sentiment = "good" if good else "bad"
        label = LABELS.get(dt, dt)
        unit = UNITS.get(dt, "")
        out.append({
            "id": f"anom-{dt}", "kind": "anomaly", "sentiment": sentiment, "metric": dt,
            "title": f"{label} {'spiked' if higher else 'dipped'} yesterday"
                     if sentiment != "good" else f"{label} stood out",
            "detail": f"{_fmt(last_v, unit)} — {abs(z):.1f}σ "
                      f"{'above' if higher else 'below'} your {_fmt(m, unit)} average.",
            "priority": 55 + abs(z) * 12 + (10 if sentiment == "bad" else 0),
        })
    return out


def _records(cache: dict[str, list[dict]]) -> list[dict[str, Any]]:
    """An all-time extreme set on the most recent day of a series."""
    out = []
    watch = {
        "steps": "up", "sleep-score": "up", "daily-heart-rate-variability": "up",
        "cardio-load": "up", "readiness": "up", "sleep-duration": "up",
    }
    for dt, best_dir in watch.items():
        s = _series(dt, cache)
        if len(s) < 12:
            continue
        vals = [v for _, v in s]
        last_v = vals[-1]
        is_high = last_v >= max(vals)
        is_low = last_v <= min(vals)
        if not (is_high or is_low):
            continue
        # Only the "good" extreme is a celebration; the bad extreme is left to anomaly.
        if best_dir == "up" and not is_high:
            continue
        if best_dir == "down" and not is_low:
            continue
        label = LABELS.get(dt, dt)
        unit = UNITS.get(dt, "")
        out.append({
            "id": f"rec-{dt}", "kind": "record", "sentiment": "good", "metric": dt,
            "title": f"New {label.lower()} record",
            "detail": f"{_fmt(last_v, unit)} is your best in {len(vals)} days of data.",
            "priority": 70,
        })
    return out


def _streaks(cache: dict[str, list[dict]]) -> list[dict[str, Any]]:
    """Consecutive most-recent days clearing a goal."""
    goals: list[tuple[str, Callable[[float], bool], str]] = [
        ("readiness", lambda v: v >= 66, "in the ready zone (66+)"),
        ("sleep-duration", lambda v: v >= 7, "of 7h+ sleep"),
        ("sleep-score", lambda v: v >= 80, "of 80+ sleep scores"),
        ("steps", lambda v: v >= 8000, "hitting 8,000+ steps"),
    ]
    out = []
    for dt, ok, phrase in goals:
        s = _series(dt, cache)
        if len(s) < 3:
            continue
        streak = 0
        for _, v in reversed(s):
            if ok(v):
                streak += 1
            else:
                break
        if streak >= 3:
            out.append({
                "id": f"streak-{dt}", "kind": "streak", "sentiment": "good", "metric": dt,
                "title": f"{streak}-day streak",
                "detail": f"{streak} days running {phrase}.",
                "priority": 50 + streak * 2,
            })
    return out


def _load_balance(cache: dict[str, list[dict]]) -> list[dict[str, Any]]:
    """Acute:chronic workload ratio (ACWR) from cardio-load.

    acute = 7-day average, chronic = 28-day average. The sweet spot 0.8–1.3 balances
    freshness and fitness; >1.5 is the classic elevated-injury-risk 'spike'; <0.8 means
    you're detraining. A widely used marker in sports science."""
    s = _series("cardio-load", cache)
    if len(s) < 14:
        return []
    vals = [v for _, v in s]
    acute = _mean(vals[-7:])
    chronic = _mean(vals[-28:])
    if not acute or not chronic:
        return []
    acwr = acute / chronic
    if acwr >= 1.5:
        sentiment, verdict = "watch", "a sharp spike — the elevated-strain zone. Consider an easier day."
    elif acwr >= 1.3:
        sentiment, verdict = "watch", "ramping up faster than your norm. Keep an eye on recovery."
    elif acwr >= 0.8:
        sentiment, verdict = "good", "right in the balanced 0.8–1.3 zone — building fitness sustainably."
    else:
        sentiment, verdict = "info", "below your habitual load — you're resting or detraining."
    return [{
        "id": "load-acwr", "kind": "load", "sentiment": sentiment, "metric": "cardio-load",
        "title": f"Training balance {acwr:.2f}",
        "detail": f"Your last 7 days of cardio load are {verdict}",
        "priority": 46 + (20 if sentiment == "watch" else 0),
        "gauge": {"value": round(acwr, 2), "zones": [0.8, 1.3, 1.5], "max": 2.0},
    }]


def _sleep_debt(cache: dict[str, list[dict]]) -> list[dict[str, Any]]:
    """Cumulative shortfall vs an 8h target over the last 7 nights."""
    s = _series("sleep-duration", cache)
    if len(s) < 5:
        return []
    last7 = [v for _, v in s[-7:]]
    debt = sum(max(0.0, 8.0 - v) for v in last7)
    surplus = sum(max(0.0, v - 8.0) for v in last7)
    net = debt - surplus
    if net >= 3:
        return [{
            "id": "sleep-debt", "kind": "sleep_debt", "sentiment": "watch",
            "metric": "sleep-duration", "title": f"{net:.1f}h sleep debt",
            "detail": f"You're {net:.1f}h short of 8h/night across the last "
                      f"{len(last7)} nights. An early night would help.",
            "priority": 44 + net * 2,
        }]
    if debt <= 1 and surplus >= 2:
        return [{
            "id": "sleep-debt", "kind": "sleep_debt", "sentiment": "good",
            "metric": "sleep-duration", "title": "Well rested",
            "detail": f"You've banked {surplus:.1f}h above 8h/night this week — no sleep debt.",
            "priority": 38,
        }]
    return []


def _consistency(cache: dict[str, list[dict]]) -> list[dict[str, Any]]:
    """How steady the sleep schedule has been (spread of nightly duration).

    Regularity is one of the strongest, most overlooked sleep-quality levers — a steady
    schedule outperforms a long-but-erratic one. Measured as the standard deviation of
    the last two weeks of nightly duration."""
    s = _series("sleep-duration", cache)
    if len(s) < 10:
        return []
    recent = [v for _, v in s[-14:]]
    sd = _std(recent)
    if sd <= 0.8:
        return [{
            "id": "sleep-consistency", "kind": "streak", "sentiment": "good",
            "metric": "sleep-duration", "title": "Consistent sleep",
            "detail": f"Your nights held to ±{sd:.1f}h across the last {len(recent)} — "
                      f"a steady schedule your body clock likes.",
            "priority": 42,
        }]
    if sd >= 1.6:
        return [{
            "id": "sleep-consistency", "kind": "anomaly", "sentiment": "watch",
            "metric": "sleep-duration", "title": "Irregular sleep",
            "detail": f"Your nights swung ±{sd:.1f}h across the last {len(recent)}. "
                      f"A steadier bedtime would lift recovery more than extra hours.",
            "priority": 48,
        }]
    return []


def _rhr_elevation(cache: dict[str, list[dict]]) -> list[dict[str, Any]]:
    """Consecutive recent days with resting HR above the habitual norm — a classic early
    marker of strain, under-recovery, or an oncoming illness."""
    s = _series("daily-resting-heart-rate", cache)
    if len(s) < 12:
        return []
    vals = [v for _, v in s]
    base = _mean(vals[-28:-4])  # habitual level, excluding the very recent days
    if base is None:
        return []
    streak = 0
    for v in reversed(vals):
        if v > base + 1.5:
            streak += 1
        else:
            break
    if streak >= 3:
        return [{
            "id": "rhr-elevated", "kind": "anomaly", "sentiment": "watch",
            "metric": "daily-resting-heart-rate", "title": "Resting HR running high",
            "detail": f"{streak} days above your {base:.0f} bpm norm — a possible sign of "
                      f"strain, thin sleep, or something coming on. Worth an easy day.",
            "priority": 62,
        }]
    return []


def _correlations(cache: dict[str, list[dict]]) -> list[dict[str, Any]]:
    """How strongly a prior-day metric tracks next-day readiness."""
    out = []
    ready = dict(_series("readiness", cache))
    pairs_spec = [
        ("sleep-duration", "Sleeping longer"),
        ("sleep-score", "A better sleep score"),
        ("cardio-load", "Heavier training"),
    ]
    for dt, phrase in pairs_spec:
        s = _series(dt, cache)
        xs, ys = [], []
        for d, v in s:
            nxt = ready.get(d + timedelta(days=1))
            if nxt is not None:
                xs.append(v)
                ys.append(nxt)
        r = _pearson(xs, ys)
        if r is None or abs(r) < 0.35:
            continue
        direction = "higher" if r > 0 else "lower"
        sentiment = "info"
        out.append({
            "id": f"corr-{dt}", "kind": "correlation", "sentiment": sentiment, "metric": dt,
            "title": f"{phrase} → {direction} readiness",
            "detail": f"Across {len(xs)} days, {phrase.lower()} tends to precede "
                      f"{direction} next-day readiness (r = {r:+.2f}).",
            "priority": 30 + abs(r) * 20,
        })
    return out


DETECTORS = [_trends, _anomalies, _records, _streaks, _load_balance, _sleep_debt,
             _consistency, _rhr_elevation, _correlations]


def compute(limit: int = 8) -> list[dict[str, Any]]:
    """Run every detector over the stored series and return the top insights by priority."""
    cache = store.query_daily_bulk()
    found: list[dict[str, Any]] = []
    for detect in DETECTORS:
        try:
            found.extend(detect(cache))
        except Exception:  # a single detector must never sink the whole feed
            continue
    found.sort(key=lambda i: i["priority"], reverse=True)
    # Cap each metric at two insights so no single metric floods the feed, but distinct
    # angles on it (e.g. sleep debt *and* irregular schedule) can both surface.
    per_metric: dict[str, int] = {}
    seen_ids: set[str] = set()
    ranked: list[dict[str, Any]] = []
    for ins in found:
        if ins["id"] in seen_ids:  # same detector, same subject — never twice
            continue
        key = ins.get("metric") or ins["id"]
        if per_metric.get(key, 0) >= 2:
            continue
        per_metric[key] = per_metric.get(key, 0) + 1
        seen_ids.add(ins["id"])
        ranked.append(ins)
    for ins in ranked:
        ins["priority"] = round(ins["priority"], 1)
    return ranked[:limit]
