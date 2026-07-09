"""Goals — targets you set, and whether you're getting there.

Where insights.py *describes* the data, this module *scores it against intent*. A goal is
a metric + a comparator (`gte` = at least, `lte` = at most) + a target. For each one we
compute the things that answer "are we getting there?":

  met_now    — does the latest reading clear the target?
  adherence  — over the last 28 days, the share of days that cleared it (the progress %)
  streak     — consecutive most-recent days clearing it
  trend      — least-squares slope over 14 days, read in the goal's favoured direction
  status     — met (≥85% adherence) / on-track (≥50% or improving) / off-track

The aggregate rolls the per-goal progress into one number + an on-track count, the
higher-level picture the dashboard shows above the individual cards.

Goals are stateless to evaluate (read straight from the stored series) and seeded with a
few sensible starters on first use so the section is alive immediately.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from . import store
from .config import REGISTRY_BY_NAME

COMPARATORS = ("gte", "lte")
_ADHERENCE_WINDOW = 28
_TREND_WINDOW = 14

# Starter goals seeded on first use (only those whose metric exists in the registry).
SEED_GOALS: list[tuple[str, str, float]] = [
    ("steps", "gte", 8000),
    ("sleep-duration", "gte", 7.5),
    ("readiness", "gte", 70),
    ("sleep-score", "gte", 80),
    ("daily-resting-heart-rate", "lte", 65),
]


def ensure_seeded() -> None:
    if store.count_goals() == 0:
        for dt, comparator, target in SEED_GOALS:
            if dt in REGISTRY_BY_NAME:
                store.add_goal(dt, comparator, target)


def _slope(ys: list[float]) -> float:
    n = len(ys)
    if n < 3:
        return 0.0
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0
    return sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / sxx


def _meets(comparator: str, value: float, target: float) -> bool:
    return value >= target if comparator == "gte" else value <= target


def _evaluate(goal: dict[str, Any], cache: dict[str, list[dict]]) -> dict[str, Any]:
    dt = REGISTRY_BY_NAME.get(goal["data_type"])
    label = dt.label if dt else goal["data_type"]
    unit = dt.unit if dt else ""
    comparator, target = goal["comparator"], goal["target"]

    rows = cache.get(goal["data_type"]) or []
    vals = sorted(
        (date.fromisoformat(r["day"]), float(r["value"]))
        for r in rows if r.get("value") is not None
    )
    base = {
        "id": goal["id"], "data_type": goal["data_type"], "label": label, "unit": unit,
        "comparator": comparator, "target": target,
    }
    if not vals:
        return {**base, "latest": None, "latest_day": None, "met_now": False,
                "adherence": 0, "streak": 0, "trend": "flat", "trend_good": False,
                "status": "no-data", "days": 0, "spark": []}

    latest_day, latest = vals[-1]
    window = vals[-_ADHERENCE_WINDOW:]
    met = sum(1 for _, v in window if _meets(comparator, v, target))
    adherence = round(met / len(window) * 100)

    streak = 0
    for _, v in reversed(vals):
        if _meets(comparator, v, target):
            streak += 1
        else:
            break

    slope = _slope([v for _, v in vals[-_TREND_WINDOW:]])
    eps = max(abs(target) * 0.002, 0.01)
    if abs(slope) < eps:
        trend, trend_good = "flat", _meets(comparator, latest, target)
    else:
        good = slope > 0 if comparator == "gte" else slope < 0
        trend, trend_good = ("improving" if good else "slipping"), good

    if adherence >= 85:
        status = "met"
    elif adherence >= 50 or trend == "improving":
        status = "on-track"
    else:
        status = "off-track"

    return {
        **base,
        "latest": round(latest, 2), "latest_day": latest_day.isoformat(),
        "met_now": _meets(comparator, latest, target),
        "adherence": adherence, "streak": streak,
        "trend": trend, "trend_good": trend_good, "status": status,
        "days": len(window), "spark": [round(v, 2) for _, v in vals[-21:]],
    }


def _summary(goals: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [g for g in goals if g["latest"] is not None]
    if not scored:
        return {"overall": 0, "on_track": 0, "total": len(goals), "scored": 0,
                "narrative": "Add a goal to start tracking."}
    overall = round(sum(g["adherence"] for g in scored) / len(scored))
    on_track = sum(1 for g in scored if g["status"] in ("met", "on-track"))
    behind = [g["label"] for g in scored if g["status"] == "off-track"]
    if on_track == len(scored):
        narrative = "Every goal is on track — keep the rhythm."
    elif behind:
        lead = ", ".join(behind[:2])
        narrative = f"{on_track} of {len(scored)} on track. {lead} need{'s' if len(behind) == 1 else ''} attention."
    else:
        narrative = f"{on_track} of {len(scored)} goals on track."
    return {"overall": overall, "on_track": on_track, "total": len(goals),
            "scored": len(scored), "narrative": narrative}


# Worst-first, like the benchmarks view: what needs work opens the section, wins close
# it. Within a band, lowest adherence leads. Every consumer (Goals tab, Overview,
# the coach's goals widget) inherits this order.
_STATUS_RANK = {"off-track": 0, "no-data": 1, "on-track": 2, "met": 3}


def evaluate_all() -> dict[str, Any]:
    """Every active goal scored against the stored data, plus the aggregate rollup."""
    ensure_seeded()
    cache = store.query_daily_bulk()
    goals = [_evaluate(g, cache) for g in store.list_goals()]
    goals.sort(key=lambda g: (_STATUS_RANK.get(g["status"], 1), g["adherence"]))
    return {"goals": goals, "summary": _summary(goals)}
