"""The daily schedule — time-anchored blocks, lived one day at a time.

A template of blocks renders as each day's timeline. Blocks recur calendar-style:
each carries the weekdays it applies to (`days`, Mon=0…Sun=6 — weekday-only gym,
weekend-only late wake) and an effect window (`starts_on`/`ends_on`). Edits follow
the Google-Calendar model: change a block everywhere, or "from day X onward" —
which ends the old version and starts a new one, so past days keep rendering
exactly as they were lived. Removal = ending, never deleting.

Per day, every applicable block can be marked done or missed and carry a note;
one-off items can be added to a single day. Month rollups answer "how is this
actually going" — per-day kept counts and per-block adherence over the days the
block actually applied.

Design stance: the user marks blocks done himself — the log is his word, on purpose.
Synced data (workouts, bed/wake times) is surfaced as context chips on the day, never
as automatic verdicts.

NOTE: no fastapi import here — the coach's MCP server (its own lean venv) imports this
module too. The HTTP endpoints live in main.py, inline, like the other features'.
"""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, datetime, timedelta

from . import store

COLORS = ("neutral", "focus", "move", "food", "wind")
ALL_DAYS = "0123456"  # Mon=0 … Sun=6, matching date.weekday()
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# Seeded once on first run from the schedule he set (July 2026). Every word and time
# is editable in the UI afterwards — this is a starting template, not doctrine.
DEFAULT_BLOCKS = [
    # (time, label, detail, color, remind)
    ("07:15", "Wake", "Same time every day, weekends included (±30 min).", "neutral", 0),
    ("07:45", "Deep rep — 90 min", "Task pre-chosen the night before.", "focus", 1),
    ("09:30", "Day work", "9:30 – 6:00.", "neutral", 0),
    ("18:15", "Gym or run", "Straight from work, no stop home.", "move", 1),
    ("19:30", "Dinner + short walk", "Real food beats willpower later.", "food", 1),
    ("20:15", "Light session", "Optional: edit, read, tinker — capped at 75 min.", "focus", 0),
    ("22:00", "Wind down + pick tomorrow's task", "No workouts after 9 pm — ever.", "wind", 1),
    ("23:15", "In bed — hard stop", "~8 h window covers the 7.7 h need.", "neutral", 1),
]


def ensure_seed() -> None:
    """First run only: install the default template. Never re-seeds — an emptied
    schedule stays empty on purpose (count includes archived blocks)."""
    if store.count_blocks() == 0:
        for time_, label, detail, color, remind in DEFAULT_BLOCKS:
            store.add_block(time_, label, detail, color, remind)


def _normalize_days(days: str) -> str:
    """Weekday digits, deduped and sorted — '650' → '056'."""
    picked = sorted(set(days or ""))
    if not picked or any(c not in ALL_DAYS for c in picked):
        raise ValueError("days must be a non-empty set of weekday digits 0(Mon)–6(Sun).")
    return "".join(picked)


def _parse_iso(name: str, s: str) -> str:
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        raise ValueError(f"Invalid {name} '{s}' — use ISO YYYY-MM-DD.")


def _validate(time_: str | None = None, label: str | None = None,
              color: str | None = None) -> None:
    if time_ is not None and not _TIME_RE.match(time_):
        raise ValueError(f"Invalid time '{time_}' — use 24h HH:MM.")
    if label is not None and not label.strip():
        raise ValueError("The block needs a label.")
    if label is not None and len(label) > 120:
        raise ValueError("Label too long (max 120 chars).")
    if color is not None and color not in COLORS:
        raise ValueError(f"color must be one of {COLORS}.")


def _applies(b: dict, iso: str, weekday: int) -> bool:
    """Does this block occur on that calendar day?"""
    if (b.get("starts_on") or b["created_at"]) > iso:
        return False
    if b.get("ends_on") and b["ends_on"] < iso:
        return False
    return str(weekday) in (b.get("days") or ALL_DAYS)


# --- template edits (the calendar-app model) ------------------------------------

def create_block(time_: str, label: str, detail: str | None = None,
                 color: str = "neutral", remind: bool = True,
                 days: str = ALL_DAYS, starts_on: str | None = None) -> dict:
    _validate(time_, label, color)
    return store.add_block(time_.strip(), label.strip(), (detail or "").strip() or None,
                           color, int(remind), _normalize_days(days),
                           _parse_iso("starts_on", starts_on) if starts_on else None)


def _clean_edit_fields(fields: dict) -> dict:
    _validate(fields.get("time"), fields.get("label"), fields.get("color"))
    if fields.get("days") is not None:
        fields["days"] = _normalize_days(fields["days"])
    if fields.get("starts_on") is not None:
        fields["starts_on"] = _parse_iso("starts_on", fields["starts_on"])
    for flag in ("remind", "active"):
        if fields.get(flag) is not None:
            fields[flag] = int(bool(fields[flag]))
    return fields


def edit_block(block_id: int, **fields) -> dict:
    """Change the block everywhere it applies (its whole window, past included)."""
    if not store.get_block(block_id):
        raise ValueError(f"No block with id {block_id}.")
    return store.update_block(block_id, **_clean_edit_fields(fields)) or {}


def edit_block_from(block_id: int, from_day: date, **fields) -> dict:
    """The 'this and following days' edit: end the current version the day before
    `from_day` and start a new block with the changes — past days keep the old
    version (their log rows stay attached to it)."""
    old = store.get_block(block_id)
    if not old:
        raise ValueError(f"No block with id {block_id}.")
    fields = _clean_edit_fields(fields)
    start = old.get("starts_on") or old["created_at"]
    if from_day.isoformat() <= start:
        return store.update_block(block_id, **fields) or {}  # window fully covered
    store.update_block(block_id, ends_on=(from_day - timedelta(days=1)).isoformat())
    merged = {k: (fields.get(k) if fields.get(k) is not None else old[k])
              for k in ("time", "label", "detail", "color", "remind", "days")}
    return store.add_block(merged["time"], merged["label"], merged["detail"],
                           merged["color"], merged["remind"], merged["days"],
                           from_day.isoformat())


def end_block(block_id: int, from_day: date) -> dict:
    """Remove the block from `from_day` onward; days before it keep rendering.
    Ending a block on/before its start leaves a never-applying tombstone (kept so
    the seeded-once check stays true)."""
    old = store.get_block(block_id)
    if not old:
        raise ValueError(f"No block with id {block_id}.")
    return store.update_block(
        block_id, ends_on=(from_day - timedelta(days=1)).isoformat()) or {}


# --- the day -------------------------------------------------------------------

def _item_from_block(b: dict, e: dict | None) -> dict:
    return {
        "id": e["id"] if e else None,        # entry id (exists once touched)
        "block_id": b["id"],
        "time": b["time"],
        "label": b["label"],
        "detail": b["detail"],
        "color": b["color"],
        "remind": bool(b["remind"]),
        "done": None if not e or e["done"] is None else bool(e["done"]),
        "note": e["note"] if e else None,
        "oneoff": False,
    }


def _item_from_oneoff(e: dict) -> dict:
    return {
        "id": e["id"],
        "block_id": None,
        "time": e["time"],
        "label": e["label"] or "—",
        "detail": e["detail"],
        "color": "neutral",
        "remind": True,
        "done": None if e["done"] is None else bool(e["done"]),
        "note": e["note"],
        "oneoff": True,
    }


def day_view(d: date) -> dict:
    """One day's timeline: the blocks that occur that day (recurrence + window)
    merged with that day's log rows, blocks logged that day even if they no longer
    occur (their history is pinned), and the day's one-off items."""
    iso = d.isoformat()
    wd = d.weekday()
    blocks = {b["id"]: b for b in store.list_blocks(include_ended=True)}
    entries = store.entries_between(iso, iso)
    by_block = {e["block_id"]: e for e in entries if e["block_id"] is not None}

    items = [_item_from_block(b, by_block.get(bid))
             for bid, b in blocks.items()
             if _applies(b, iso, wd) or bid in by_block]
    items += [_item_from_oneoff(e) for e in entries if e["block_id"] is None]
    items.sort(key=lambda x: (x["time"] or "99:99", x["block_id"] or 10**9))

    return {
        "date": iso,
        "items": items,
        "done": sum(1 for x in items if x["done"] is True),
        "total": len(items),
        "context": _day_context(d),
    }


def _day_context(d: date) -> dict:
    """What the synced data saw that day — chips, not verdicts."""
    iso = d.isoformat()
    ctx: dict = {"trained": None, "bed": None, "wake": None}
    try:
        span = max(1, (date.today() - d).days + 1)
        day_workouts = [w for w in store.query_workouts(days=span, limit=500)
                        if w["day"] == iso]
        if day_workouts:
            mins = round(sum(w["duration_min"] or 0 for w in day_workouts))
            first = min(w["start_local"] for w in day_workouts)
            ctx["trained"] = f"{mins} min · started {first[11:16]}"
        night = max(store.query_sleep_sessions(iso, iso),
                    key=lambda s: s["duration_min"] or 0, default=None)
        if night and night.get("start_local") and night.get("end_local"):
            ctx["bed"] = night["start_local"][11:16]
            ctx["wake"] = night["end_local"][11:16]
    except Exception:  # context is decoration — never break the day view over it
        pass
    return ctx


# --- logging -------------------------------------------------------------------

def log(day: date, block_id: int | None = None, entry_id: int | None = None,
        done: bool | None = None, note: str | None = None) -> dict:
    """Mark a block done/missed and/or set its note. Template blocks are addressed by
    block_id; one-off items by entry_id."""
    fields: dict = {}
    if done is not None:
        fields["done"] = int(done)
    if note is not None:
        fields["note"] = note.strip() or None
    if not fields:
        raise ValueError("Nothing to log — pass done and/or note.")

    if block_id is not None:
        if not store.get_block(block_id):
            raise ValueError(f"No block with id {block_id}.")
        return store.upsert_block_entry(day.isoformat(), block_id, **fields)
    if entry_id is not None:
        e = store.get_entry(entry_id)
        if not e:
            raise ValueError(f"No entry with id {entry_id}.")
        return store.update_entry(entry_id, **fields) or {}
    raise ValueError("Pass block_id (template block) or entry_id (one-off item).")


def add_oneoff(day: date, time_: str, label: str, detail: str | None = None) -> dict:
    _validate(time_, label)
    return store.add_oneoff_entry(day.isoformat(), time_.strip(), label.strip(),
                                  (detail or "").strip() or None)


def remove_oneoff(entry_id: int) -> None:
    e = store.get_entry(entry_id)
    if not e:
        raise ValueError(f"No entry with id {entry_id}.")
    if e["block_id"] is not None:
        raise ValueError("Only one-off items can be deleted — archive template blocks instead.")
    store.delete_entry(entry_id)


# --- rollups (drill up) ---------------------------------------------------------

def month_view(year: int, month: int, today: date | None = None) -> dict:
    """The calendar month: per-day kept counts plus per-block adherence over the
    days each block actually occurred (its weekdays, inside its window)."""
    today = today or date.today()
    ndays = monthrange(year, month)[1]
    first, last = date(year, month, 1), date(year, month, ndays)
    blocks = store.list_blocks(include_ended=True)
    entries = store.entries_between(first.isoformat(), min(last, today).isoformat())

    by_day: dict[str, list[dict]] = {}
    for e in entries:
        by_day.setdefault(e["day"], []).append(e)
    block_ids = {b["id"] for b in blocks}

    days, kept, of = [], 0, 0
    occur_days: dict[int, int] = {}   # block_id -> days it occurred so far this month
    for n in range(1, ndays + 1):
        d = date(year, month, n)
        iso = d.isoformat()
        if d > today:
            expected = sum(1 for b in blocks if _applies(b, iso, d.weekday()))
            days.append({"date": iso, "done": 0, "total": expected, "future": True})
            continue
        es = by_day.get(iso, [])
        applying = {b["id"] for b in blocks if _applies(b, iso, d.weekday())}
        for bid in applying:
            occur_days[bid] = occur_days.get(bid, 0) + 1
        # Logged rows count even when their block no longer occurs (pinned history);
        # unknown block_ids (never possible today, defensive) are ignored.
        extra = {e["block_id"] for e in es
                 if e["block_id"] is not None and e["block_id"] not in applying
                 and e["block_id"] in block_ids}
        oneoffs = sum(1 for e in es if e["block_id"] is None)
        total = len(applying) + len(extra) + oneoffs
        done = sum(1 for e in es if e["done"] == 1)
        days.append({"date": iso, "done": done, "total": total, "future": False})
        if total:
            kept += done
            of += total

    block_stats = []
    for b in blocks:
        span = occur_days.get(b["id"], 0)
        if span <= 0:
            continue
        done_days = sum(1 for e in entries if e["block_id"] == b["id"] and e["done"] == 1)
        block_stats.append({
            "block_id": b["id"], "label": b["label"], "color": b["color"],
            "done": done_days, "days": span, "pct": round(done_days / span * 100),
        })

    return {
        "month": f"{year:04d}-{month:02d}",
        "days": days,
        "kept": kept,
        "of": of,
        "pct": round(kept / of * 100) if of else None,
        "block_stats": block_stats,
    }


# --- reminders -------------------------------------------------------------------

REMIND_AHEAD_MIN = 16  # quarter-hour launchd runs → each block fires once, 1-15 min out


def due_reminders(now: datetime) -> list[tuple[str, str, str]]:
    """(key, title, body) for blocks starting within the look-ahead window that aren't
    done yet and want reminding. Keys are per-day, so state-file dedup is enough."""
    view = day_view(now.date())
    out = []
    for item in view["items"]:
        if not item["time"] or not item["remind"] or item["done"] is True:
            continue
        h, m = int(item["time"][:2]), int(item["time"][3:])
        starts = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if not (timedelta(0) < starts - now <= timedelta(minutes=REMIND_AHEAD_MIN)):
            continue
        ref = item["block_id"] if item["block_id"] is not None else f"e{item['id']}"
        out.append((
            f"sched:{view['date']}:{ref}",
            f"{item['label']} — {item['time']}",
            item["detail"] or "On the schedule. Small and done beats perfect and skipped.",
        ))
    return out


# --- briefing evidence ------------------------------------------------------------

def today_evidence() -> dict:
    v = day_view(date.today())
    return {
        "what": "His daily schedule of time-anchored blocks; he marks each done or "
                "missed himself. Kept blocks are the day's real score.",
        "date": v["date"],
        "kept": v["done"],
        "total": v["total"],
        "items": [{"time": x["time"], "label": x["label"], "done": x["done"]}
                  for x in v["items"]],
    }


def week_evidence(this_start: date, this_end: date,
                  prev_start: date, prev_end: date) -> dict:
    def block(start: date, end: date) -> dict:
        days = []
        d = start
        while d <= min(end, date.today()):
            v = day_view(d)
            days.append({"date": v["date"], "done": v["done"], "total": v["total"]})
            d += timedelta(days=1)
        kept = sum(x["done"] for x in days)
        of = sum(x["total"] for x in days)
        return {"days": days, "kept": kept, "of": of,
                "pct": round(kept / of * 100) if of else None}
    return {
        "what": "Daily schedule adherence: blocks marked done vs the day's blocks.",
        "this_week": block(this_start, this_end),
        "prev_week": block(prev_start, prev_end),
    }
