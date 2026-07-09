"""Coach — from numbers to a plan.

Readiness scores your recovery, insights describe what's happening, goals track targets.
This module closes the loop: it reads all of that and answers the only question that
changes behaviour — *what should I do today?* — as a short, ranked list of concrete,
personal recommendations.

Each rule looks at the current state (today's readiness components, this week's sleep,
training-load balance, resting-HR drift, goal adherence) and, when it fires, contributes
one recommendation with an imperative title and a plain-English "why". The list is ranked
so the single most useful action leads, and capped so it stays a focus, not a to-do dump.

This is our own heuristic coaching, not medical advice.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any

from . import goals, readiness, store

# Tone drives the card colour on the dashboard.
#   push    (lime)  — capacity to do more
#   rest    (rose)  — pull back / recover
#   improve (amber) — a gap worth closing
#   steady  (cyan)  — maintain

def _series(data_type: str, cache: dict[str, list[dict]]) -> list[tuple[date, float]]:
    return sorted(
        (date.fromisoformat(r["day"]), float(r["value"]))
        for r in (cache.get(data_type) or []) if r.get("value") is not None
    )


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _std(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 else (f"{v:.0f}" if abs(v - round(v)) < 0.05 else f"{v:.1f}")


def recommend(limit: int = 3) -> dict[str, Any]:
    """Ranked 'what to do today' recommendations synthesised from the current state."""
    cache = store.query_daily_bulk()
    recs: list[dict[str, Any]] = []

    rb = readiness.today_breakdown()
    score = rb["score"] if rb else None
    comps = {c["key"]: c for c in rb["components"]} if rb else {}
    rhr_low = "rhr" in comps and comps["rhr"]["score"] < 55
    temp_hot = "temp" in comps and comps["temp"]["score"] < 55

    sleep = _series("sleep-duration", cache)
    last7 = [v for _, v in sleep[-7:]]
    debt = (sum(max(0.0, 8 - v) for v in last7) - sum(max(0.0, v - 8) for v in last7)) if len(sleep) >= 5 else 0.0
    sd = _std([v for _, v in sleep[-14:]]) if len(sleep) >= 10 else 0.0

    load = _series("cardio-load", cache)
    acwr = None
    if len(load) >= 14:
        a, c = _mean([v for _, v in load[-7:]]), _mean([v for _, v in load[-28:]])
        acwr = a / c if a and c else None

    geval = goals.evaluate_all()
    behind = [g for g in geval["goals"] if g["status"] == "off-track"]

    # --- rules, most consequential first -------------------------------------

    # 1. Recover — the body is asking for a lighter day.
    if score is not None and (score < 55 or rhr_low or temp_hot):
        why = []
        if score < 55:
            why.append(f"readiness is {score}")
        if rhr_low:
            why.append(f"resting HR is up at {_fmt(comps['rhr']['value'])} bpm")
        if temp_hot:
            why.append(f"skin temperature is {_fmt(abs(comps['temp']['value']))}°C off baseline")
        recs.append({
            "id": "recover", "category": "Recover", "tone": "rest", "metric": "readiness",
            "title": "Keep today easy",
            "detail": f"Your body's asking for it — {', and '.join(why)}. Favour gentle "
                      f"movement, hydration, and an early night over anything hard.",
            "priority": 95,
        })

    # 2. Push — high readiness with room in the tank.
    elif score is not None and score >= 78 and (acwr is None or acwr < 0.95):
        load_note = f" and your training load is light (balance {acwr:.2f})" if acwr else ""
        recs.append({
            "id": "push", "category": "Train", "tone": "push", "metric": "cardio-load",
            "title": "Green light to push",
            "detail": f"Readiness is {score}{load_note} — a good day for a harder session "
                      f"if you want one. Your body has the capacity.",
            "priority": 78,
        })

    # 3. Sleep — the highest-leverage fix when it's off.
    if debt >= 3:
        recs.append({
            "id": "sleep-debt", "category": "Sleep", "tone": "improve", "metric": "sleep-duration",
            "title": "Make sleep the priority tonight",
            "detail": f"You're about {debt:.1f}h short of 8h across the last week. Catching "
                      f"up pays back across recovery, mood, and tomorrow's readiness more "
                      f"than any single workout.",
            "priority": 80,
        })
    elif sd >= 1.6:
        recs.append({
            "id": "sleep-regular", "category": "Sleep", "tone": "improve", "metric": "sleep-duration",
            "title": "Steady your bedtime",
            "detail": f"Your nights are swinging ±{sd:.1f}h. A consistent sleep and wake time "
                      f"— even on weekends — lifts sleep quality more than extra hours do.",
            "priority": 60,
        })

    # 4. Move — a goal is lagging and you have the capacity to close it.
    if behind and (score is None or score >= 60):
        g = max(behind, key=lambda x: x["target"] - (x["latest"] or 0) if x["comparator"] == "gte" else 0)
        recs.append({
            "id": f"move-{g['data_type']}", "category": "Move", "tone": "improve", "metric": g["data_type"],
            "title": f"Chip away at your {g['label'].lower()} goal",
            "detail": f"You're clearing it on only {g['adherence']}% of days "
                      f"({g['comparator'] == 'gte' and 'at least' or 'at most'} "
                      f"{_fmt(g['target'])}{' ' + g['unit'] if g['unit'] else ''}). A little "
                      f"deliberate effort today moves the trend.",
            "priority": 55,
        })

    # 5. Maintain — nothing's flagged; reinforce the streak.
    if not recs:
        recs.append({
            "id": "steady", "category": "Steady", "tone": "steady", "metric": "readiness",
            "title": "You're dialled in — hold the line",
            "detail": (f"Readiness {score} and nothing's flagging. " if score else "")
                      + f"{geval['summary']['on_track']} of {geval['summary']['scored']} goals "
                        f"are on track. Keep the rhythm you've built.",
            "priority": 30,
        })

    recs.sort(key=lambda r: r["priority"], reverse=True)
    return {"date": rb["date"] if rb else date.today().isoformat(),
            "recommendations": recs[:limit]}
