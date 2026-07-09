"""Daily briefing — an LLM synthesis layer over the deterministic insights engine.

The detectors in insights.py stay the source of truth for *facts* (they do the real
statistics). This module packs their uncapped output — plus readiness, sleep, goals,
benchmarks, and 30-day summaries — into one evidence JSON, hands it to the tool-less
`fitbit-analyst` opencode agent, and stores the returned {headline, narrative, insights}
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
from .chat import REPO_ROOT, _opencode_bin, _plain, resolve_model
from .config import REGISTRY, REGISTRY_BY_NAME

AGENT = "fitbit-analyst"
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


def build_evidence() -> dict[str, Any]:
    bulk = store.query_daily_bulk()
    return {
        "date": date.today().isoformat(),
        "profile": "Male, mid-20s. Stated aims: train more consistently, sleep better.",
        "system": _system_status(),
        "signals": insights.compute(limit=24),  # the detectors' (near-)full output
        "readiness": readiness.today_breakdown(),
        "sleep": _slim_sleep(sleep_analysis.detail()),
        "goals": _slim_goals(goals.evaluate_all()),
        "benchmarks": _slim_benchmarks(benchmarks.evaluate_all()),
        "summary_30d": _summary_30d(bulk),
    }


# --- the model call ----------------------------------------------------------------

def _run_analyst(evidence_json: str, model: str, nudge: str = "") -> str:
    """One tool-less opencode run; returns the concatenated text output."""
    message = evidence_json if not nudge else f"{evidence_json}\n\n{nudge}"
    cmd = [_opencode_bin(), "run", "--format", "json", "--agent", AGENT,
           "-m", model, "--", message]
    try:
        proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True,
                              text=True, timeout=_TIMEOUT)
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
    narrative = str(raw.get("narrative") or "").strip()[:600]
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

def latest() -> dict | None:
    with store._connect() as conn:
        row = conn.execute(
            "SELECT day, generated_at, model, headline, narrative, insights, evidence_digest "
            "FROM briefings ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    out = dict(row)
    out["insights"] = json.loads(out["insights"])
    out.pop("evidence_digest", None)
    return out


def generate(force: bool = False) -> dict:
    """Build evidence, skip if unchanged (unless forced), else run the analyst and store."""
    init_db()
    evidence = build_evidence()
    evidence_json = json.dumps(evidence, separators=(",", ":"))
    digest = hashlib.sha256(evidence_json.encode()).hexdigest()

    if not force:
        with store._connect() as conn:
            row = conn.execute(
                "SELECT evidence_digest FROM briefings ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row and row["evidence_digest"] == digest:
            return latest()  # nothing new since last time — reuse

    model = resolve_model()  # survives free-tier lineup rotation
    try:
        briefing = _parse(_run_analyst(evidence_json, model))
    except (BriefingError, json.JSONDecodeError):
        # One stern retry — free models occasionally wrap or truncate the JSON.
        briefing = _parse(_run_analyst(
            evidence_json, model,
            "REMINDER: reply with ONLY the JSON object — no fences, no prose around it."))

    with store._connect() as conn:
        conn.execute(
            "INSERT INTO briefings (day, generated_at, model, headline, narrative, insights, evidence_digest) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (evidence["date"], datetime.now(timezone.utc).isoformat(), model,
             briefing["headline"], briefing["narrative"],
             json.dumps(briefing["insights"]), digest),
        )
    return latest()
