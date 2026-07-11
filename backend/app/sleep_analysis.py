"""Sleep deep-dive — the narrative behind the nightly score.

The dashboard already stores a nightly Sleep Score and the four stage durations. This module
reads them closely: how last night broke down, how your stage mix compares to evidence-based
targets, your rolling sleep debt against need, how *regular* your schedule is (one of the
strongest and most-overlooked levers), and where the trend is heading.

Everything is computed on request from the stored daily series — stateless, like readiness.

Evidence bases:
  - Sleep need: personalized — the median of your own recent nights, held inside the
    National Sleep Foundation 7–9 h band for adults 18–25. The clamp matters: a raw
    average of chronically short sleep would enshrine the deprivation as the goal.
    Until enough nights exist, need falls back to the band's 8 h anchor.
  - Stage proportions of total sleep: Deep (N3) ~13–23 %, REM ~20–25 %, Light (N1+N2) ~50–60 %
    (StatPearls adult norms; stage needs shift with age).
  - Sleep debt: rolling 14-night deficit vs need (Van Dongen 2003), with asymmetric recovery —
    a single long night only partly repays accumulated debt.
  - Tonight's target: need + a capped fraction of the debt (recovery sleep saturates; you
    cannot bank a week's shortfall in one night) + a bump after a hard training day.
  - Regularity: night-to-night duration spread — a steady schedule supports circadian health
    independent of total hours.
  - Efficiency: ≥85 % normal, ≥90 % excellent (sleep-medicine convention).
"""
from __future__ import annotations

import math
from datetime import date

from . import store

NEED_BAND = (7.0, 9.0)      # NSF adults 18–25
NEED_FALLBACK = 8.0         # band anchor, used until NEED_MIN_NIGHTS exist
NEED_MIN_NIGHTS = 14
NEED_WINDOW = 60            # nights of history the personal median draws on
# Sub-3h records are naps and partial captures (the tracker logs fragmented nights as
# separate short sleeps), not main sleep. They stay in the charts but are excluded
# from need/debt/regularity math — a 2h artifact would otherwise mint ~5h of fake debt.
MIN_NIGHT_HOURS = 3.0
TONIGHT_DEBT_SHARE = 0.35   # fraction of current debt to pay down tonight…
TONIGHT_DEBT_CAP = 1.0      # …but never more than this (hours)
TONIGHT_CEIL = 9.5          # sanity ceiling on any prescription

STAGE_TARGETS = {
    # pct-of-total-sleep bands, and whether the metric is "more is better" up to the band.
    "sleep-deep": {"label": "Deep", "lo": 13, "hi": 23},
    "sleep-rem": {"label": "REM", "lo": 20, "hi": 25},
    "sleep-light": {"label": "Light", "lo": 50, "hi": 60},
}


# --- tiny stats toolkit (kept local so this module stands alone) --------------

def _series(cache: dict[str, list[dict]], key: str, positive: bool = False) -> list[tuple[str, float]]:
    out = [(r["day"], float(r["value"]))
           for r in cache.get(key, []) if r.get("value") is not None]
    if positive:
        out = [(d, v) for d, v in out if v > 0]
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


def _linfit_change(ys: list[float]) -> float:
    """Total least-squares drift across the window (slope × span)."""
    n = len(ys)
    if n < 3:
        return 0.0
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / sxx
    return slope * (n - 1)


def _round(v: float | None, n: int = 1) -> float | None:
    return round(v, n) if v is not None else None


def _hm(hours: float) -> str:
    """7.28 → '7h 15m' at a 5-minute grain — targets read as clock time, not decimals."""
    m = round(hours * 60 / 5) * 5
    h, mm = divmod(m, 60)
    return f"{h}h {mm:02d}m" if mm else f"{h}h"


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def personal_need(dvals: list[float]) -> dict:
    """Sleep need from the sleeper's own record, kept honest by the evidence band.

    Median (robust to the odd all-nighter or lie-in) of the last NEED_WINDOW valid
    nights, clamped to NEED_BAND — your average can reflect what you *managed*, not
    what you *need*, so chronic restriction never becomes the target. Shared with
    insights so every debt number in the app agrees."""
    window = [v for v in dvals if v >= MIN_NIGHT_HOURS][-NEED_WINDOW:]
    if len(window) < NEED_MIN_NIGHTS:
        return {"hours": NEED_FALLBACK, "source": "population", "median": None,
                "nights": len(window), "window": NEED_WINDOW,
                "band": list(NEED_BAND), "clamped": False}
    med = _median(window)
    hours = min(max(med, NEED_BAND[0]), NEED_BAND[1])
    return {"hours": round(hours, 2), "source": "personal", "median": round(med, 2),
            "nights": len(window), "window": NEED_WINDOW,
            "band": list(NEED_BAND), "clamped": abs(hours - med) > 1e-9}


# --- the analysis -------------------------------------------------------------

def detail() -> dict | None:
    """Full sleep breakdown, or None if there aren't enough nights yet."""
    cache = store.query_daily_bulk()
    dur = _series(cache, "sleep-duration", positive=True)
    if len(dur) < 3:
        return None

    by_day = {d: v for d, v in dur}
    deep = dict(_series(cache, "sleep-deep"))
    rem = dict(_series(cache, "sleep-rem"))
    light = dict(_series(cache, "sleep-light"))
    awake = dict(_series(cache, "sleep-awake"))
    eff = dict(_series(cache, "sleep-efficiency"))
    score = dict(_series(cache, "sleep-score"))

    last_day = dur[-1][0]

    # --- last night ---
    ln_dur = by_day[last_day]
    ln_stages = {
        "deep": _round(deep.get(last_day)),
        "rem": _round(rem.get(last_day)),
        "light": _round(light.get(last_day)),
        "awake": _round(awake.get(last_day)),
    }
    asleep = sum(v for k, v in ln_stages.items() if k != "awake" and v) or ln_dur
    stage_pct = {
        k: round((ln_stages[k] / asleep) * 100, 1)
        for k in ("deep", "rem", "light")
        if ln_stages[k] is not None and asleep
    }
    last_night = {
        "day": last_day,
        "duration": _round(ln_dur),
        "efficiency": _round(eff.get(last_day)),
        "score": _round(score.get(last_day), 0),
        "stages": ln_stages,
        "stage_pct": stage_pct,
    }

    # --- rolling averages ---
    dvals = [v for _, v in dur]

    def avg(series_map: dict[str, float] | list[tuple[str, float]], n: int) -> float | None:
        vals = [v for _, v in (series_map if isinstance(series_map, list) else sorted(series_map.items()))]
        vals = [v for v in vals if v is not None]
        return _round(_mean(vals[-n:]))

    averages = {
        "duration_7": avg(dur, 7), "duration_28": avg(dur, 28),
        "efficiency_7": avg(list(eff.items()), 7),
        "score_7": avg(list(score.items()), 7),
        "deep_14": avg(list(deep.items()), 14),
        "rem_14": avg(list(rem.items()), 14),
        "light_14": avg(list(light.items()), 14),
    }

    # --- stage mix vs targets (averaged over recent nights for stability) ---
    recent_days = [d for d, _ in dur[-14:]]
    stage_report = []
    for key, spec in STAGE_TARGETS.items():
        smap = {"sleep-deep": deep, "sleep-rem": rem, "sleep-light": light}[key]
        pcts = []
        for d in recent_days:
            tot = sum(x for x in (deep.get(d), rem.get(d), light.get(d)) if x)
            if tot and smap.get(d) is not None:
                pcts.append(smap[d] / tot * 100)
        if not pcts:
            continue
        pct = round(sum(pcts) / len(pcts), 1)
        if pct < spec["lo"]:
            tone, verdict = "under", "below the healthy band"
        elif pct > spec["hi"]:
            tone, verdict = "high", "above the typical band"
        else:
            tone, verdict = "good", "right in the healthy band"
        stage_report.append({
            "key": key.replace("sleep-", ""), "label": spec["label"],
            "pct": pct, "target_lo": spec["lo"], "target_hi": spec["hi"],
            "tone": tone,
            "note": f"{spec['label']} is {pct:.0f}% of your sleep — {verdict} "
                    f"({spec['lo']}–{spec['hi']}%).",
        })

    # --- personalized need (median of your own nights, clamped to the NSF band) ---
    basis = personal_need(dvals)
    need = basis["hours"]

    # Analysis series: artifact "nights" out (charts/last-night still show the record).
    dvals_valid = [v for v in dvals if v >= MIN_NIGHT_HOURS]

    # --- sleep debt vs need (rolling 14 nights; Van Dongen 2003) ---
    # Recovery is asymmetric — one long night doesn't erase accumulated debt — so banked
    # surplus earns only half credit against the deficit.
    last14 = dvals_valid[-14:]
    deficit = sum(max(0.0, need - v) for v in last14)
    surplus = sum(max(0.0, v - need) for v in last14)
    net = round(deficit - 0.5 * surplus, 1)
    n14 = len(last14)
    if net >= 5:
        debt_tone, debt_msg = "watch", (
            f"You're carrying about {net:.1f}h of sleep debt over the last {n14} nights. "
            "A few earlier nights would start clearing it.")
    elif net >= 2:
        debt_tone, debt_msg = "typical", (
            f"A modest {net:.1f}h of sleep debt across {n14} nights — recoverable with a "
            "couple of fuller nights.")
    elif net <= 0:
        debt_tone, debt_msg = "good", (
            f"No sleep debt — you're at or above your {need:.1f}h need across {n14} nights.")
    else:
        debt_tone, debt_msg = "good", (
            f"Only {net:.1f}h behind over {n14} nights — essentially on top of your need.")
    debt_info = {"hours": net, "nights": n14, "tone": debt_tone,
                 "need": need, "message": debt_msg}

    # --- tonight's target: need + partial debt paydown + today's training ---
    # Recovery sleep saturates, so only a capped share of the debt is prescribed per
    # night; the rest clears over the following nights.
    payback = round(min(TONIGHT_DEBT_SHARE * max(net, 0.0), TONIGHT_DEBT_CAP), 2)
    load_bump = 0.0
    load = _series(cache, "cardio-load", positive=True) or _series(cache, "active-zone-minutes", positive=True)
    if load and load[-1][0] == date.today().isoformat():
        today_load = load[-1][1]
        base_vals = [v for _, v in load[-29:-1]]
        base_load = _mean(base_vals)
        if base_load and today_load >= 1.35 * base_load:
            load_bump = 0.25
    raw_total = need + payback + load_bump
    tonight_total = round(min(raw_total, TONIGHT_CEIL), 2)
    if payback + load_bump <= 0:
        tn_tone = "good"
        tn_msg = (f"No debt to repay — hold your {_hm(need)} baseline and bank the "
                  "consistency.")
    else:
        tn_tone = "typical" if payback + load_bump <= 0.75 else "watch"
        why = [f"{_hm(payback)} toward your {net:.1f}h debt"] if payback else []
        if load_bump:
            why.append("a little extra after today's hard training")
        tn_msg = (f"Aim for about {_hm(tonight_total)} tonight — your {_hm(need)} need "
                  f"plus {' and '.join(why)}. The rest of the debt clears over the "
                  "next few nights.")
    tonight = {
        "hours": tonight_total, "need": need, "debt_payback": payback,
        "load_bump": load_bump, "capped": raw_total > TONIGHT_CEIL,
        "tone": tn_tone, "message": tn_msg,
    }

    # --- regularity (spread of nightly duration) ---
    reg_window = dvals_valid[-14:]
    sd = round(_std(reg_window), 2)
    if sd <= 0.8:
        reg_tone, reg_msg = "good", (
            f"Steady — your nightly sleep length held to ±{sd:.1f}h across the last "
            f"{len(reg_window)} nights. Consistency is one of sleep's biggest, quietest wins.")
    elif sd >= 1.6:
        reg_tone, reg_msg = "watch", (
            f"Uneven — your sleep length swung ±{sd:.1f}h across the last {len(reg_window)} "
            "nights. A more regular schedule tends to lift recovery more than the odd long lie-in.")
    else:
        reg_tone, reg_msg = "typical", (
            f"Fairly even at ±{sd:.1f}h over the last {len(reg_window)} nights — a little room to tighten.")
    regularity = {"std": sd, "nights": len(reg_window), "tone": reg_tone, "message": reg_msg}

    # --- trend (duration, last 14) ---
    change = round(_linfit_change(reg_window), 2)
    if abs(change) < 0.4:
        tr_dir, tr_tone = "steady", "typical"
    elif change > 0:
        tr_dir, tr_tone = "rising", "good"
    else:
        tr_dir, tr_tone = "easing", "watch"
    trend = {"metric": "duration", "direction": tr_dir, "change": change, "tone": tr_tone,
             "nights": len(reg_window)}

    # --- nightly array for charts (last 28) ---
    nights = []
    for d, v in dur[-28:]:
        nights.append({
            "day": d, "duration": _round(v),
            "score": _round(score.get(d), 0), "efficiency": _round(eff.get(d)),
            "deep": _round(deep.get(d)), "rem": _round(rem.get(d)),
            "light": _round(light.get(d)), "awake": _round(awake.get(d)),
        })

    return {
        "as_of": last_day,
        "need_hours": need,
        "need_basis": basis,
        "tonight": tonight,
        "last_night": last_night,
        "averages": averages,
        "stage_targets": stage_report,
        "debt": debt_info,
        "regularity": regularity,
        "trend": trend,
        "nights": nights,
    }
