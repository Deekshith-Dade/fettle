"""Daily briefing — an LLM synthesis layer over the deterministic insights engine.

The detectors in insights.py stay the source of truth for *facts* (they do the real
statistics). This module packs their uncapped output — plus readiness, sleep, goals,
benchmarks, and 30-day summaries — into one evidence JSON, hands it to the tool-less
`fettle-analyst` opencode agent, and stores the returned {headline, narrative, insights}
so the dashboard renders it instantly. Generated after each sync (cli.py) and on demand
(POST /api/briefing/refresh); an evidence digest skips regeneration when nothing changed.

Every number in the briefing must come from the evidence pack — the agent has no tools
and is instructed to never compute; validation drops malformed cards.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import auth, benchmarks, goals, insights, readiness, sleep_analysis, store
from .chat import REPO_ROOT, _opencode_bin, _opencode_env, _plain, resolve_model
from .config import REGISTRY, REGISTRY_BY_NAME

AGENT = "fettle-analyst"
_TIMEOUT = 180
MAX_CARDS = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS briefings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    day             TEXT NOT NULL,      -- local ISO date the briefing is about
    generated_at    TEXT NOT NULL,
    model           TEXT,
    headline        TEXT NOT NULL,
    narrative       TEXT NOT NULL,
    insights        TEXT NOT NULL,      -- JSON array of validated cards
    evidence_digest TEXT
);
"""


def init_db() -> None:
    with store._connect() as conn:
        conn.executescript(SCHEMA)
        # v2: daily and weekly briefings share the table, discriminated by `kind`.
        cols = [r[1] for r in conn.execute("PRAGMA table_info(briefings)").fetchall()]
        if "kind" not in cols:
            conn.execute("ALTER TABLE briefings ADD COLUMN kind TEXT NOT NULL DEFAULT 'daily'")


class BriefingError(RuntimeError):
    pass


# --- evidence pack ---------------------------------------------------------------

def _slim_sleep(detail: dict | None) -> dict | None:
    if not detail:
        return None
    slim = dict(detail)
    slim.pop("nights", None)  # per-night array is bulky and already summarized
    return slim


def _slim_goals(evaluated: dict) -> dict:
    out = {"summary": evaluated.get("summary"), "goals": []}
    for g in evaluated.get("goals", []):
        out["goals"].append({k: g.get(k) for k in
                             ("data_type", "label", "unit", "comparator", "target",
                              "latest", "adherence", "streak", "trend", "status")})
    return out


def _slim_benchmarks(evaluated: dict) -> list[dict]:
    return [
        {k: b.get(k) for k in ("key", "label", "unit", "value", "tier", "tone", "target")}
        for b in evaluated.get("benchmarks", [])
    ]


def _summary_30d(bulk: dict[str, list[dict]], days: int = 30) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    out = []
    for dt in REGISTRY:
        vals = [r["value"] for r in bulk.get(dt.api_name, [])
                if r["value"] is not None and r["day"] >= cutoff]
        if not vals:
            continue
        out.append({
            "metric": dt.api_name, "label": dt.label, "unit": dt.unit,
            "latest": round(vals[-1], 2), "mean": round(sum(vals) / len(vals), 2),
            "min": round(min(vals), 2), "max": round(max(vals), 2), "days": len(vals),
        })
    return out


def _system_status() -> dict:
    """Data-trust context: token lifetime and sync freshness. A dead token or stale
    sync quietly poisons every other read, so the analyst is told to lead with it."""
    rows = store.sync_status()
    last = max((r["last_sync_at"] for r in rows if r["last_sync_at"]), default=None)
    hours = None
    if last:
        try:
            hours = round(
                (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 3600, 1)
        except ValueError:
            pass
    return {
        "authenticated": auth.has_valid_token(),
        "token_days_left": auth.token_days_left(),
        "hours_since_last_sync": hours,
    }


def _user_context() -> list[dict]:
    """Durable facts the user told the coach (injuries, schedule, preferences) — the
    briefing should respect them (e.g. don't prescribe running over a sore knee)."""
    return [
        {"category": m["category"], "content": m["content"], "since": m["created_at"][:10]}
        for m in store.list_memories()
    ]


def _previous_briefing() -> dict | None:
    """The most recent daily briefing from an *earlier* day — storyline context, so
    today's read can continue a narrative instead of rediscovering it."""
    with store._connect() as conn:
        row = conn.execute(
            "SELECT day, headline, narrative, insights FROM briefings "
            "WHERE kind='daily' AND day < ? ORDER BY id DESC LIMIT 1",
            (date.today().isoformat(),),
        ).fetchone()
    if not row:
        return None
    return {
        "day": row["day"], "headline": row["headline"], "narrative": row["narrative"],
        "insight_titles": [c.get("title") for c in json.loads(row["insights"])],
    }


def build_evidence() -> dict[str, Any]:
    bulk = store.query_daily_bulk()
    return {
        "mode": "daily",
        "date": date.today().isoformat(),
        "profile": "Male, mid-20s. Stated aims: train more consistently, sleep better.",
        "user_context": _user_context(),
        "previous_briefing": _previous_briefing(),
        "system": _system_status(),
        "signals": insights.compute(limit=24),  # the detectors' (near-)full output
        "readiness": readiness.today_breakdown(),
        "sleep": _slim_sleep(sleep_analysis.detail()),
        "goals": _slim_goals(goals.evaluate_all()),
        "benchmarks": _slim_benchmarks(benchmarks.evaluate_all()),
        "summary_30d": _summary_30d(bulk),
    }


# --- weekly retrospective evidence ---------------------------------------------------

def _window_vals(bulk: dict, name: str, start: date, end: date) -> list[float]:
    s, e = start.isoformat(), end.isoformat()
    return [r["value"] for r in bulk.get(name, [])
            if r["value"] is not None and s <= r["day"] <= e]


def _agg(vals: list[float]) -> dict | None:
    if not vals:
        return None
    return {"mean": round(sum(vals) / len(vals), 2),
            "total": round(sum(vals), 1), "days": len(vals)}


def build_weekly_evidence() -> dict[str, Any]:
    """Trailing 7 days vs the 7 before — the week's story in aggregates, not raw series."""
    today = date.today()
    this_start, prev_start, prev_end = (today - timedelta(days=6),
                                        today - timedelta(days=13),
                                        today - timedelta(days=7))
    bulk = store.query_daily_bulk()

    metrics = []
    for dt in REGISTRY:
        cur = _window_vals(bulk, dt.api_name, this_start, today)
        prev = _window_vals(bulk, dt.api_name, prev_start, prev_end)
        if not cur and not prev:
            continue
        metrics.append({"metric": dt.api_name, "label": dt.label, "unit": dt.unit,
                        "this_week": _agg(cur), "prev_week": _agg(prev)})

    goal_rows = []
    for g in store.list_goals():
        dt = REGISTRY_BY_NAME.get(g["data_type"])

        def rate(start: date, end: date) -> dict | None:
            vals = _window_vals(bulk, g["data_type"], start, end)
            if not vals:
                return None
            met = sum(1 for v in vals
                      if (v >= g["target"] if g["comparator"] == "gte" else v <= g["target"]))
            return {"days_met": met, "days": len(vals),
                    "rate_pct": round(met / len(vals) * 100)}

        goal_rows.append({
            "metric": g["data_type"], "label": dt.label if dt else g["data_type"],
            "comparator": g["comparator"], "target": g["target"],
            "this_week": rate(this_start, today), "prev_week": rate(prev_start, prev_end),
        })

    sessions = store.query_workouts(days=14, limit=80)

    def week_workouts(start: date, end: date, keep_list: bool) -> dict:
        rows = [w for w in sessions if start.isoformat() <= w["day"] <= end.isoformat()]
        out: dict[str, Any] = {
            "sessions": len(rows),
            "minutes": round(sum(w["duration_min"] or 0 for w in rows), 1),
            "calories": round(sum(w["calories"] or 0 for w in rows)),
            "by_activity": {
                a: round(sum(w["duration_min"] or 0 for w in rows if w["activity"] == a), 1)
                for a in sorted({w["activity"] for w in rows if w["activity"]})
            },
        }
        if keep_list:
            out["list"] = [{"day": w["day"], "activity": w["activity"],
                            "duration_min": w["duration_min"]} for w in rows]
        return out

    return {
        "mode": "weekly-retrospective",
        "week": {"start": this_start.isoformat(), "end": today.isoformat()},
        "previous_week": {"start": prev_start.isoformat(), "end": prev_end.isoformat()},
        "profile": "Male, mid-20s. Stated aims: train more consistently, sleep better.",
        "user_context": _user_context(),
        "system": _system_status(),
        "metrics_week_over_week": metrics,
        "goals_week_over_week": goal_rows,
        "workouts": {"this_week": week_workouts(this_start, today, keep_list=True),
                     "prev_week": week_workouts(prev_start, prev_end, keep_list=False)},
    }


# --- the model call ----------------------------------------------------------------

def _run_analyst(evidence_json: str, model: str, nudge: str = "") -> str:
    """One tool-less opencode run; returns the concatenated text output."""
    message = evidence_json if not nudge else f"{evidence_json}\n\n{nudge}"
    cmd = [_opencode_bin(), "run", "--format", "json", "--agent", AGENT,
           "-m", model, "--", message]
    try:
        proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=_opencode_env(),
                              capture_output=True, text=True, timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise BriefingError("analyst run timed out")
    if proc.returncode != 0:
        raise BriefingError(f"analyst run failed: {_plain(proc.stderr)[-300:]}")
    texts = []
    for line in proc.stdout.splitlines():
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if evt.get("type") == "text":
            texts.append((evt.get("part") or {}).get("text") or "")
    return "\n".join(texts)


def _parse(raw_text: str) -> dict:
    """Extract and validate the briefing JSON (fence- and prose-tolerant)."""
    text = re.sub(r"```(?:json)?", "", raw_text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise BriefingError("no JSON object in analyst output")
    raw = json.loads(text[start:end + 1])

    headline = str(raw.get("headline") or "").strip()[:120]
    narrative = str(raw.get("narrative") or "").strip()
    if len(narrative) > 600:
        # Clip at the last full sentence inside the cap, not mid-word.
        cut = narrative[:600]
        narrative = cut[:cut.rfind(".") + 1] or cut
    cards = []
    for c in raw.get("insights") or []:
        if not isinstance(c, dict):
            continue
        title = str(c.get("title") or "").strip()[:90]
        detail = str(c.get("detail") or "").strip()[:450]
        if not title or not detail:
            continue
        sentiment = c.get("sentiment")
        if sentiment not in {"good", "watch", "bad", "info"}:
            sentiment = "info"
        metric = c.get("metric")
        if metric not in REGISTRY_BY_NAME:
            metric = None
        card = {"id": f"llm-{len(cards)}", "kind": "llm", "sentiment": sentiment,
                "title": title, "detail": detail, "priority": 0}
        if metric:
            card["metric"] = metric
        cards.append(card)
        if len(cards) >= MAX_CARDS:
            break
    if not headline or not narrative or not cards:
        raise BriefingError("briefing incomplete after validation")
    return {"headline": headline, "narrative": narrative, "insights": cards}


# --- persistence + orchestration ----------------------------------------------------

def latest(kind: str = "daily") -> dict | None:
    with store._connect() as conn:
        row = conn.execute(
            "SELECT kind, day, generated_at, model, headline, narrative, insights, evidence_digest "
            "FROM briefings WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)
        ).fetchone()
    if not row:
        return None
    out = dict(row)
    out["insights"] = json.loads(out["insights"])
    out.pop("evidence_digest", None)
    return out


def _generate(kind: str, evidence: dict, force: bool) -> dict:
    """Shared core: skip when the evidence digest is unchanged (unless forced), else
    run the analyst, validate, store under `kind`."""
    evidence_json = json.dumps(evidence, separators=(",", ":"))
    digest = hashlib.sha256(evidence_json.encode()).hexdigest()

    if not force:
        with store._connect() as conn:
            row = conn.execute(
                "SELECT evidence_digest FROM briefings WHERE kind=? ORDER BY id DESC LIMIT 1",
                (kind,),
            ).fetchone()
        if row and row["evidence_digest"] == digest:
            return latest(kind)  # nothing new since last time — reuse

    model = resolve_model()  # survives free-tier lineup rotation
    try:
        briefing = _parse(_run_analyst(evidence_json, model))
    except (BriefingError, json.JSONDecodeError):
        # One stern retry — free models occasionally wrap or truncate the JSON.
        briefing = _parse(_run_analyst(
            evidence_json, model,
            "REMINDER: reply with ONLY the JSON object — no fences, no prose around it."))

    day = evidence.get("date") or (evidence.get("week") or {}).get("end")
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO briefings (kind, day, generated_at, model, headline, narrative, insights, evidence_digest) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, day, datetime.now(timezone.utc).isoformat(), model,
             briefing["headline"], briefing["narrative"],
             json.dumps(briefing["insights"]), digest),
        )
    return latest(kind)


def generate(force: bool = False) -> dict:
    """The daily briefing (see module docstring)."""
    init_db()
    return _generate("daily", build_evidence(), force)


def generate_weekly(force: bool = False) -> dict:
    """The weekly retrospective: this week vs last, goals, and one thing to change."""
    init_db()
    return _generate("weekly", build_weekly_evidence(), force)
