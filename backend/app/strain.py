"""Strain — today's cardiovascular exertion as a 0-100%, plus the day's optimal band.

The third of the daily-rings trio (Strain / Recovery / Sleep). Recovery is `readiness`
and Sleep is `sleep-score`; both already exist. Strain is new: fettle already derives
`cardio-load` (a TRIMP-style day total: 1·fat-burn + 2·cardio + 3·peak active-zone
minutes), so Strain just places today's load on the person's own recent scale.

  score = 100 · today_load / ref,  ref = 90th-percentile load over the last 90 days
                                        (floored, so sparse early data can't over-scale)

100% ≈ one of your hardest recent days; a rest day sits near 0. A personal scale (not a
population one) is the honest choice — "hard" means hard *for you*.

Optimal band (the hatched target arc, WHOOP's idea): how much strain today's recovery can
absorb. Well-recovered → push; run-down → keep it easy. A transparent linear map of the
readiness score, not a validated load-prescription:

  target = [recovery·0.55, recovery·0.85]   (both clamped 0-100)

Orientation and motivation, not medical or training-prescription advice.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from . import readiness, store

REF_WINDOW = 90        # days of history the personal scale is built from
REF_FLOOR = 60.0       # minimum reference load — a moderate day, so early data can't peg 100%
REF_PCT = 90           # percentile of load that maps to 100% strain


def _percentile(values: list[float], pct: int) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    i = max(0, min(len(s) - 1, math.ceil(pct / 100 * len(s)) - 1))
    return s[i]


def _load_series(today: date) -> list[tuple[str, float]]:
    start = today - timedelta(days=REF_WINDOW)
    return [(r["day"], r["value"]) for r in store.query_daily("cardio-load", start, None)
            if r["value"] is not None]


def today(now: date | None = None) -> dict | None:
    """Today's strain ring, or None if there's no cardio-load history to scale against."""
    now = now or date.today()
    series = _load_series(now)
    if not series:
        return None

    ref = max(_percentile([v for _, v in series], REF_PCT), REF_FLOOR)
    last_day, load = series[-1]
    score = round(min(100.0, max(0.0, 100.0 * load / ref)))

    # Optimal band from today's recovery (if we can score it).
    rec = readiness.today_breakdown()
    target = None
    if rec is not None:
        r = rec["score"]
        lo = round(max(0.0, min(100.0, r * 0.55)))
        hi = round(max(0.0, min(100.0, r * 0.85)))
        target = {"lo": lo, "hi": hi}

    tone, detail = _read(score, target)
    return {
        "day": last_day, "score": score, "load": round(load), "load_unit": "load",
        "ref": round(ref), "target": target, "tone": tone, "detail": detail,
    }


def _read(score: int, target: dict | None) -> tuple[str, str]:
    """A one-line interpretation and a tone (under|optimal|over) vs today's band."""
    if not target:
        return "neutral", f"You're at {score}% of a hard day's cardiovascular load."
    if score < target["lo"]:
        return "under", (f"Light so far ({score}%). Your recovery can absorb more — "
                         f"today's optimal zone is {target['lo']}–{target['hi']}%.")
    if score > target["hi"]:
        return "over", (f"Hard day ({score}%), above today's {target['lo']}–{target['hi']}% "
                        "optimal zone — bank some recovery.")
    return "optimal", f"Right in today's optimal {target['lo']}–{target['hi']}% zone."
