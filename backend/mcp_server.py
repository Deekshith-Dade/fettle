"""fitbit-plus MCP server — the health database + analysis engine, exposed as tools.

An LLM agent (driven via the opencode CLI) calls these to answer questions about the
user's health data. Design stance: every tool wraps an existing, deterministic function
from the `app` package — readiness, insights, coach, benchmarks, sleep, and the raw
series. The model *orchestrates and narrates*; it never recomputes statistics itself
(free/open models are unreliable at that, and the Python already does it correctly, with
citations). Metric arguments are a closed enum built from the registry, so the model
physically cannot pass a metric name that doesn't exist.

Launched by opencode as a local (stdio) MCP server — see the repo-root opencode.json.
Run standalone for a sanity check:  python backend/mcp_server.py  (then Ctrl-C).
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from enum import Enum

# Make the backend package importable regardless of opencode's working directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import (  # noqa: E402
    benchmarks, coach, config, goals, insights, readiness, sleep_analysis, store,
)
from app.config import REGISTRY, REGISTRY_BY_NAME  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

store.init_db()  # ensure the schema/connection is valid before serving

mcp = FastMCP("fitbit-plus")

# Closed vocabulary of metric names. Member values are the real api_names ("daily-resting-
# heart-rate", …); the model sees them as the allowed enum and can't invent one.
MetricName = Enum("MetricName", {dt.field_name: dt.api_name for dt in REGISTRY})


def _has_intraday(dt) -> bool:
    # Mirror the dashboard's rule: daily-summary and derived types have no real sub-daily stream.
    return dt.supports_intraday and not dt.daily_via_list and not dt.derived


# --- catalog & raw series ----------------------------------------------------

@mcp.tool()
def list_metrics() -> list[dict]:
    """List every metric this person's data can answer questions about.

    Returns each metric's `name` (use it verbatim in the other tools), human `label`,
    `unit`, dashboard `group`, whether it has an intraday (sub-daily) stream, and whether
    it is a derived score. Call this first when unsure which metric name to query."""
    return [
        {
            "name": dt.api_name,
            "label": dt.label,
            "unit": dt.unit,
            "group": config.group_for(dt.api_name),
            "intraday": _has_intraday(dt),
            "derived": dt.derived,
        }
        for dt in REGISTRY
    ]


@mcp.tool()
def get_summary(days: int = 30) -> list[dict]:
    """The whole picture in one call: per-metric summary stats over the last `days` (default 30).

    For every metric with data in the window, returns latest value, mean, min, max, the
    day-count, and a coarse trend ('up' / 'down' / 'flat' — recent half vs earlier half).
    Use this to get oriented before drilling into a specific metric with get_metric."""
    bulk = store.query_daily_bulk()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    out: list[dict] = []
    for dt in REGISTRY:
        vals = [r["value"] for r in bulk.get(dt.api_name, [])
                if r["value"] is not None and r["day"] >= cutoff]
        if not vals:
            continue
        n = len(vals)
        trend = "flat"
        if n >= 6:
            half = n // 2
            early = sum(vals[:half]) / half
            late = sum(vals[half:]) / (n - half)
            if early:
                rel = (late - early) / abs(early)
                trend = "up" if rel > 0.05 else "down" if rel < -0.05 else "flat"
        out.append({
            "metric": dt.api_name, "label": dt.label, "unit": dt.unit,
            "group": config.group_for(dt.api_name),
            "latest": round(vals[-1], 2), "mean": round(sum(vals) / n, 2),
            "min": round(min(vals), 2), "max": round(max(vals), 2),
            "days_with_data": n, "trend": trend,
        })
    return out


@mcp.tool()
def get_metric(metric: MetricName, days: int = 30) -> dict:
    """Daily time series for one metric over the last `days` (default 30).

    Returns {metric, label, unit, points:[{day, value}]}, oldest first. Use get_summary or
    list_metrics first if unsure of the name. Pass a larger `days` for a longer history."""
    name = metric.value
    dt = REGISTRY_BY_NAME[name]
    start = date.today() - timedelta(days=days)
    points = store.query_daily(name, start, None)
    return {
        "metric": name, "label": dt.label, "unit": dt.unit,
        "points": [{"day": p["day"], "value": p["value"]} for p in points],
    }


@mcp.tool()
def get_intraday(metric: MetricName, day: str) -> dict:
    """Sub-daily (intraday) readings for one metric on a single `day` (ISO 'YYYY-MM-DD').

    Only some metrics have intraday streams (see list_metrics `intraday` flag) — e.g.
    heart-rate. Returns up to ~1500 evenly-downsampled points {ts, value}."""
    name = metric.value
    dt = REGISTRY_BY_NAME[name]
    if not _has_intraday(dt):
        return {"error": f"{name} has no intraday stream — it's a daily-only or derived metric."}
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return {"error": f"Invalid day '{day}'. Use ISO format YYYY-MM-DD."}
    points = store.query_intraday(name, d, d, max_points=1500)
    return {
        "metric": name, "label": dt.label, "unit": dt.unit, "day": day,
        "points": [{"ts": p["ts"], "value": p["value"]} for p in points],
    }


# --- the analysis engine (already-computed, cited interpretation) ------------

@mcp.tool()
def get_readiness() -> dict:
    """Today's recovery/readiness score (0-100) with its transparent component breakdown
    (HRV, resting HR, sleep, training load, skin temp), each scored vs the personal
    baseline, plus a plain-English narrative. Prefer this over computing recovery yourself."""
    return readiness.today_breakdown() or {"error": "Not enough data to compute readiness yet."}


@mcp.tool()
def get_insights(limit: int = 8) -> list[dict]:
    """Ranked plain-English observations the analysis engine found in the data: trends,
    anomalies, all-time records, streaks, training-load balance (ACWR), sleep debt,
    schedule regularity, resting-HR drift, correlations, and a multi-vital early-warning
    check. Each has a title, detail, sentiment (good/watch/bad/info), and the metric."""
    return insights.compute(limit=limit)


@mcp.tool()
def get_coach(limit: int = 3) -> dict:
    """'What should I do today?' — concrete, ranked recommendations synthesised from
    readiness, this week's sleep, training-load balance, and goal adherence. Each carries
    a tone (push/rest/improve/steady) and a plain-English why. Coaching, not medical advice."""
    return coach.recommend(limit=limit)


@mcp.tool()
def get_benchmarks() -> dict:
    """Where the person's habitual values stand against evidence-based reference norms for a
    healthy adult in their mid-20s (resting HR, HRV, steps, sleep duration & efficiency,
    weekly activity, breathing rate, SpO2, BMI): the tier they're in and the next rung to
    aim for. Each benchmark cites its basis. Peer context is directional, not a verdict."""
    return benchmarks.evaluate_all()


@mcp.tool()
def get_sleep() -> dict:
    """Deep-dive on sleep: last night's stage breakdown, stage mix vs evidence-based targets,
    rolling sleep debt vs need (Van Dongen), schedule regularity, and the recent trend."""
    return sleep_analysis.detail() or {"error": "Not enough sleep data yet."}


@mcp.tool()
def get_goals() -> dict:
    """The person's active goals, each scored against the data (adherence %, on/off track),
    plus an aggregate rollup of how many are on track."""
    return goals.evaluate_all()


# --- goal management (the only write tools; everything else is read-only) -------

class Comparator(str, Enum):
    gte = "gte"  # "at least" — floors, e.g. steps ≥ 8000
    lte = "lte"  # "at most"  — ceilings, e.g. sedentary hours ≤ 8


def _goal_phrase(name: str, comparator: str, target: float) -> str:
    dt = REGISTRY_BY_NAME[name]
    word = "at least" if comparator == "gte" else "at most"
    return f"{dt.label} {word} {target:g}{f' {dt.unit}' if dt.unit else ''}"


@mcp.tool()
def create_goal(metric: MetricName, comparator: Comparator, target: float) -> dict:
    """Create a new goal: 'gte' = at least (floors, e.g. steps >= 8000), 'lte' = at most
    (ceilings, e.g. sedentary hours <= 8). One goal per metric — if one already exists
    this errors with its id; use update_goal instead. Ground the target in his data
    (get_summary baseline, get_benchmarks next rung) and prefer the next reachable step
    over a leap. After any goal change, call show_goals so he sees the result."""
    name = metric.value
    if target <= 0:
        return {"error": "Target must be a positive number."}
    existing = next((g for g in store.list_goals() if g["data_type"] == name), None)
    if existing:
        return {"error": f"A goal for {name} already exists — id {existing['id']}: "
                         f"{_goal_phrase(name, existing['comparator'], existing['target'])}. "
                         "Use update_goal to change it."}
    gid = store.add_goal(name, comparator.value, float(target))
    return {"ok": True, "id": gid, "goal": _goal_phrase(name, comparator.value, target)}


@mcp.tool()
def update_goal(goal_id: int, target: float | None = None,
                comparator: Comparator | None = None) -> dict:
    """Change an existing goal's target and/or comparator. Get the goal_id from
    get_goals first. After the change, call show_goals so he sees the result."""
    g = next((x for x in store.list_goals() if x["id"] == goal_id), None)
    if not g:
        return {"error": f"No active goal with id {goal_id} — call get_goals for current ids."}
    if target is not None and target <= 0:
        return {"error": "Target must be a positive number."}
    store.update_goal(goal_id, target=target,
                      comparator=comparator.value if comparator else None)
    new_cmp = comparator.value if comparator else g["comparator"]
    new_target = target if target is not None else g["target"]
    return {"ok": True, "id": goal_id,
            "was": _goal_phrase(g["data_type"], g["comparator"], g["target"]),
            "now": _goal_phrase(g["data_type"], new_cmp, new_target)}


@mcp.tool()
def delete_goal(goal_id: int) -> dict:
    """Remove a goal permanently. Destructive — only when he clearly asked to remove or
    stop tracking it; if at all ambiguous, ask one short confirming question first.
    Get the goal_id from get_goals. After deleting, call show_goals."""
    g = next((x for x in store.list_goals() if x["id"] == goal_id), None)
    if not g:
        return {"error": f"No active goal with id {goal_id} — call get_goals for current ids."}
    store.delete_goal(goal_id)
    return {"ok": True, "removed": _goal_phrase(g["data_type"], g["comparator"], g["target"])}


# --- display tools -------------------------------------------------------------
# These render live widgets inline in the chat UI. The chat bridge watches for these
# calls and tells the frontend to mount the matching dashboard component, which fetches
# fresh data itself — the return value is only an ack that steers the model's prose.

BenchmarkKey = Enum(
    "BenchmarkKey", {b.key.replace("-", "_"): b.key for b in benchmarks.BENCHMARKS}
)


def _shown(desc: str) -> str:
    return (f"[{desc} is now displayed inline — the user can see it. "
            "Add a short interpretation or takeaway; do not restate the numbers it shows.]")


@mcp.tool()
def show_chart(metric: MetricName, days: int = 30) -> str:
    """Show an interactive chart of one metric's daily history inline in the chat.

    The default visual whenever the user asks to see data, or a trend is central to the
    answer (windows over ~45 days render as weekly averages). Look at the data first
    (get_metric / get_summary) so your commentary matches what the chart shows."""
    days = max(7, min(365, days))
    return _shown(f"Chart of {REGISTRY_BY_NAME[metric.value].label} over the last {days} days")


@mcp.tool()
def show_comparison(metric_a: MetricName, metric_b: MetricName, days: int = 30) -> str:
    """Show two metrics tracked together on one dual-axis chart — for "does X move with Y"
    questions, e.g. sleep duration vs readiness, or training load vs HRV."""
    days = max(7, min(365, days))
    la = REGISTRY_BY_NAME[metric_a.value].label
    lb = REGISTRY_BY_NAME[metric_b.value].label
    return _shown(f"Comparison chart of {la} vs {lb} over the last {days} days")


@mcp.tool()
def show_stat(metric: MetricName) -> str:
    """Show a compact stat tile for one metric: latest value, change vs its 28-day
    baseline, and a sparkline. Good for quick "where is X right now" answers."""
    return _shown(f"Stat tile for {REGISTRY_BY_NAME[metric.value].label}")


@mcp.tool()
def show_readiness() -> str:
    """Show today's readiness ring with its component drivers (HRV, resting HR, sleep,
    training load, skin temp). Use when discussing today's recovery state."""
    return _shown("Today's readiness ring with its drivers")


@mcp.tool()
def show_sleep(nights: int = 14) -> str:
    """Show nightly sleep-stage composition bars (deep / light / REM / awake) for the
    last `nights` nights. Use for sleep-quality and stage-mix discussions."""
    nights = max(5, min(28, nights))
    return _shown(f"Sleep-stage bars for the last {nights} nights")


@mcp.tool()
def show_benchmark(metric: BenchmarkKey) -> str:
    """Show where the user stands on the evidence-based reference bands for one
    benchmark: their tier, the banded scale, and the next target rung. Use for
    "how do I compare / where do I stand" questions."""
    return _shown(f"Benchmark standing for {metric.value}")


@mcp.tool()
def show_goals() -> str:
    """Show the user's active goals with live progress (adherence, streaks, status).
    Use when discussing goal progress."""
    return _shown("Goal progress overview")


if __name__ == "__main__":
    mcp.run()  # stdio transport (what opencode launches)
