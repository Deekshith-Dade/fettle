"""SQLite storage: tidy tables for charts + raw JSON so nothing is lost.

Health API value fields are type-specific union shapes (StepsRollupValue,
HeartRateRollupValue, ...). Rather than hard-code every shape, we keep a best-effort
numeric `value` for charting *and* the full `raw` JSON for later refinement.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_metrics (
    data_type TEXT NOT NULL,
    day       TEXT NOT NULL,          -- ISO date
    value     REAL,                   -- best-effort numeric
    unit      TEXT,
    raw       TEXT NOT NULL,          -- full rollup point JSON
    PRIMARY KEY (data_type, day)
);

CREATE TABLE IF NOT EXISTS intraday_points (
    data_type TEXT NOT NULL,
    ts        TEXT NOT NULL,          -- ISO datetime
    value     REAL,
    unit      TEXT,
    raw       TEXT NOT NULL,
    PRIMARY KEY (data_type, ts)
);

CREATE INDEX IF NOT EXISTS idx_intraday_type_ts ON intraday_points (data_type, ts);

-- Per (data_type, kind) high-water mark so each sync only fetches new days.
CREATE TABLE IF NOT EXISTS sync_state (
    data_type    TEXT NOT NULL,
    kind         TEXT NOT NULL,       -- 'daily' | 'intraday'
    last_day     TEXT,                -- last fully-synced ISO date
    last_sync_at TEXT,
    PRIMARY KEY (data_type, kind)
);

-- User-defined targets for a metric, monitored on the Goals dashboard.
CREATE TABLE IF NOT EXISTS goals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    data_type  TEXT NOT NULL,
    comparator TEXT NOT NULL,         -- 'gte' (at least) | 'lte' (at most)
    target     REAL NOT NULL,
    created_at TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1
);

-- Durable facts the coach is told in chat (injuries, schedule, preferences). The coach
-- recalls these at the start of a conversation; the briefing reads them as context.
CREATE TABLE IF NOT EXISTS coach_memory (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    category   TEXT NOT NULL DEFAULT 'other',   -- injury|schedule|preference|event|other
    content    TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1       -- soft delete: forgotten, not erased
);

-- Individual exercise sessions (the daily exercise-* metrics are their aggregates).
CREATE TABLE IF NOT EXISTS workout_sessions (
    id           TEXT PRIMARY KEY,    -- API dataPoint name (stable across syncs)
    day          TEXT NOT NULL,       -- local civil date of the start
    start_ts     TEXT NOT NULL,       -- UTC
    start_local  TEXT NOT NULL,       -- naive local ISO for display
    end_ts       TEXT,
    activity     TEXT,
    duration_min REAL,                -- active duration (pauses excluded)
    calories     REAL,
    distance_km  REAL,
    steps        INTEGER,
    avg_hr       REAL,
    azm          REAL,
    raw          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workouts_day ON workout_sessions (day);

-- Individual sleep sessions with their clock times. The sleep-* daily metrics keep only
-- durations; the rep tracker needs *when* — did the night start by the bed anchor and
-- end by the wake anchor.
CREATE TABLE IF NOT EXISTS sleep_sessions (
    id           TEXT PRIMARY KEY,    -- API dataPoint name (stable across syncs)
    day          TEXT NOT NULL,       -- local civil date of the wake (Fitbit dates sleep to wake day)
    start_ts     TEXT NOT NULL,       -- UTC
    start_local  TEXT NOT NULL,       -- naive local ISO (bed time)
    end_ts       TEXT,
    end_local    TEXT,                -- naive local ISO (wake time)
    duration_min REAL,                -- time asleep (falls back to in-bed span)
    raw          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sleep_sessions_day ON sleep_sessions (day);

-- The daily schedule template: time-anchored blocks each day is lived (and scored)
-- against. A block recurs on its `days` (weekday digits, Mon=0…Sun=6) inside its
-- [starts_on, ends_on] window; ending a block instead of deleting it keeps past
-- days rendering exactly as they were lived — the calendar-app edit model.
CREATE TABLE IF NOT EXISTS schedule_blocks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    time       TEXT NOT NULL,               -- "HH:MM", local
    label      TEXT NOT NULL,
    detail     TEXT,
    color      TEXT NOT NULL DEFAULT 'neutral',  -- neutral|focus|move|food|wind
    remind     INTEGER NOT NULL DEFAULT 1,  -- send the pre-block nudge for this one
    active     INTEGER NOT NULL DEFAULT 1,  -- legacy; ends_on is the removal mechanism
    days       TEXT NOT NULL DEFAULT '0123456',  -- weekdays it applies to (Mon=0…Sun=6)
    starts_on  TEXT,                        -- ISO date the block takes effect
    ends_on    TEXT,                        -- ISO date it last applies (NULL = open)
    created_at TEXT NOT NULL,
    updated_at TEXT
);

-- Per-day log: a row appears when a block gets touched (done/missed/note), plus
-- one-off items added to a single day (block_id NULL, carrying their own time/label).
CREATE TABLE IF NOT EXISTS schedule_entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    day        TEXT NOT NULL,
    block_id   INTEGER,
    time       TEXT,
    label      TEXT,
    detail     TEXT,
    done       INTEGER,                     -- 1 done, 0 missed, NULL unlogged
    note       TEXT,
    updated_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sched_day_block
    ON schedule_entries (day, block_id) WHERE block_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sched_day ON schedule_entries (day);

-- Web-push subscriptions (his phone / other browsers). One row per endpoint;
-- pruned automatically when the push service reports the subscription gone.
CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint     TEXT PRIMARY KEY,
    subscription TEXT NOT NULL,     -- full subscription JSON (keys incl.)
    created_at   TEXT NOT NULL,
    last_ok      TEXT
);

-- v2 of the accountability feature: the rep_days prototype was replaced by the
-- schedule tables above before it ever held data.
DROP TABLE IF EXISTS rep_days;
"""


def init_db(path: Path | None = None) -> None:
    with _connect(path) as conn:
        # WAL lets the dashboard keep reading while a (possibly scheduled) sync writes.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        # v2 of schedule_blocks: recurrence (days) + effect window (starts_on/ends_on).
        cols = [r[1] for r in conn.execute("PRAGMA table_info(schedule_blocks)").fetchall()]
        if "days" not in cols:
            conn.execute("ALTER TABLE schedule_blocks ADD COLUMN days TEXT NOT NULL DEFAULT '0123456'")
            conn.execute("ALTER TABLE schedule_blocks ADD COLUMN starts_on TEXT")
            conn.execute("ALTER TABLE schedule_blocks ADD COLUMN ends_on TEXT")
            conn.execute("UPDATE schedule_blocks SET starts_on=created_at WHERE starts_on IS NULL")
            # Anything archived under the old model reads as ended the day it was made.
            conn.execute("UPDATE schedule_blocks SET ends_on=created_at "
                         "WHERE active=0 AND ends_on IS NULL")


@contextmanager
def _connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path or settings.db_file)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")  # ride out a concurrent writer's commit
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Sub-objects that carry timestamps / provenance, not the metric value. We skip these
# when hunting for the number so we don't mistake a civil-date `year` for a reading.
_NON_VALUE_KEYS = frozenset({
    "sampleTime", "civilTime", "civilStartTime", "civilEndTime", "physicalTime",
    "utcOffset", "timeInterval", "sessionTimeInterval", "startTime", "endTime",
    "date", "time", "dataSource", "dataSourceId",
})
# Keys that name the actual reading, tried first so aggregates win over stray numbers.
_VALUE_KEYS = (
    "beatsPerMinute", "countSum", "total", "count", "sum",
    "average", "avg", "mean", "value", "fpVal", "intVal", "millis",
)


def _extract_number(obj: Any, sum_fields: bool = False) -> float | None:
    """Best-effort: pull the most representative number out of a value union.

    Skips timestamp/provenance sub-objects, parses numeric strings (the API returns
    integers as JSON strings), and prefers named aggregate keys over the first leaf.
    With `sum_fields`, returns the sum of *every* numeric leaf instead (e.g. active-zone-
    minutes, whose value is fat-burn + cardio + peak zone sums).
    """
    if isinstance(obj, bool):
        return None
    if isinstance(obj, (int, float)):
        return float(obj)
    if isinstance(obj, str):
        try:
            return float(obj)
        except ValueError:
            # Google protobuf Duration renders as e.g. "20040s" — strip the unit suffix.
            if obj.endswith("s"):
                try:
                    return float(obj[:-1])
                except ValueError:
                    return None
            return None
    if isinstance(obj, dict):
        clean = {
            k: v for k, v in obj.items()
            if k not in _NON_VALUE_KEYS and not k.endswith("Metadata")
        }
        if sum_fields:
            leaves = [_extract_number(v, True) for v in clean.values()]
            nums = [n for n in leaves if n is not None]
            return sum(nums) if nums else None
        for key in _VALUE_KEYS:
            if key in clean:
                found = _extract_number(clean[key])
                if found is not None:
                    return found
        for v in clean.values():
            found = _extract_number(v)
            if found is not None:
                return found
    if isinstance(obj, list):
        found_list = [_extract_number(item, sum_fields) for item in obj]
        nums = [n for n in found_list if n is not None]
        if sum_fields:
            return sum(nums) if nums else None
        return nums[0] if nums else None
    return None


def _find_key(obj: Any, key: str) -> float | None:
    """Locate `key` anywhere in a nested point and return its numeric value."""
    if isinstance(obj, dict):
        if key in obj:
            return _extract_number(obj[key])
        for v in obj.values():
            found = _find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_key(item, key)
            if found is not None:
                return found
    return None


def _find_date(obj: Any) -> date | None:
    """Find the day a daily point belongs to. Handles both dailyRollUp points
    (`civilStartTime.date`) and `daily-*` list points (`{camelType}.date`) by locating the
    first {year, month, day} CivilDate, preferring the start-of-window keys."""
    if not isinstance(obj, dict):
        return None
    if {"year", "month", "day"} <= obj.keys():
        return date(obj["year"], obj["month"], obj["day"])
    for key in ("civilStartTime", "date", "civilEndTime"):
        if key in obj:
            found = _find_date(obj[key])
            if found:
                return found
    for v in obj.values():
        found = _find_date(v)
        if found:
            return found
    return None


def upsert_daily(
    data_type: str,
    unit: str,
    points: list[dict[str, Any]],
    scale: float = 1.0,
    sum_fields: bool = False,
    diff_fields: tuple[str, str] | None = None,
) -> int:
    """Store daily points (dailyRollUp or `daily-*` list). Returns rows written.

    Value/date extraction skips timestamp & provenance keys (see `_NON_VALUE_KEYS`), so the
    same path works for both shapes. `scale`/`sum_fields`/`diff_fields` come from the
    DataType; `diff_fields` yields first-minus-second (e.g. skin temp nightly − baseline)."""
    rows = []
    for p in points:
        day = _find_date(p)
        if not day:
            continue
        if diff_fields:
            a, b = _find_key(p, diff_fields[0]), _find_key(p, diff_fields[1])
            num = a - b if a is not None and b is not None else None
        else:
            num = _extract_number(p, sum_fields=sum_fields)
        value = num * scale if num is not None else None
        rows.append((data_type, day.isoformat(), value, unit, json.dumps(p)))
    if not rows:
        return 0
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO daily_metrics (data_type, day, value, unit, raw) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(data_type, day) DO UPDATE SET "
            "value=excluded.value, unit=excluded.unit, raw=excluded.raw",
            rows,
        )
    return len(rows)


def upsert_daily_values(data_type: str, unit: str, day_values: dict[str, float]) -> int:
    """Store pre-computed per-day values (used by the sleep/exercise session processors).

    `day_values` maps ISO date -> value. The raw column keeps the computed number so the
    in-place recompute path can skip these (they have no API raw to re-parse)."""
    rows = [
        (data_type, day, value, unit, json.dumps({"derived": True, "value": value}))
        for day, value in day_values.items()
    ]
    if not rows:
        return 0
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO daily_metrics (data_type, day, value, unit, raw) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(data_type, day) DO UPDATE SET "
            "value=excluded.value, unit=excluded.unit, raw=excluded.raw",
            rows,
        )
    return len(rows)


def _point_timestamp(p: dict[str, Any]) -> str | None:
    """Find an ISO timestamp inside an intraday data point (sample/interval/session)."""
    data = {k: v for k, v in p.items() if k not in ("name", "dataSource")}
    for value in data.values():
        if not isinstance(value, dict):
            continue
        for shape_key in ("sampleTime", "timeInterval", "sessionTimeInterval"):
            shape = value.get(shape_key)
            if isinstance(shape, dict):
                ts = shape.get("physicalTime") or shape.get("startTime")
                if ts:
                    return ts
    return None


_INTRADAY_BATCH = 5000

def upsert_intraday(data_type: str, unit: str, points: Iterable[dict[str, Any]]) -> int:
    """Consume an iterable of points and upsert in batches, so a big backfill (heart-rate
    is ~50k points/day) never materializes the whole fetch in memory. Each batch commits
    independently; upserts are idempotent and the watermark only advances afterwards, so a
    mid-stream failure just means a cheap re-fetch next run."""
    sql = (
        "INSERT INTO intraday_points (data_type, ts, value, unit, raw) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(data_type, ts) DO UPDATE SET "
        "value=excluded.value, unit=excluded.unit, raw=excluded.raw"
    )
    total = 0
    batch: list[tuple] = []

    def flush() -> None:
        nonlocal total
        if batch:
            with _connect() as conn:
                conn.executemany(sql, batch)
            total += len(batch)
            batch.clear()

    for p in points:
        ts = _point_timestamp(p)
        if not ts:
            continue
        value = _extract_number({k: v for k, v in p.items()
                                 if k not in ("name", "dataSource")})
        batch.append((data_type, ts, value, unit, json.dumps(p)))
        if len(batch) >= _INTRADAY_BATCH:
            flush()
    flush()
    return total


# --- sync watermarks ---------------------------------------------------------

def get_watermark(data_type: str, kind: str) -> date | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT last_day FROM sync_state WHERE data_type=? AND kind=?",
            (data_type, kind),
        ).fetchone()
    if row and row["last_day"]:
        return date.fromisoformat(row["last_day"])
    return None


def set_watermark(data_type: str, kind: str, last_day: date) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sync_state (data_type, kind, last_day, last_sync_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(data_type, kind) DO UPDATE SET "
            "last_day=excluded.last_day, last_sync_at=excluded.last_sync_at",
            (data_type, kind, last_day.isoformat(), datetime.now(timezone.utc).isoformat()),
        )


# --- read helpers for the API ------------------------------------------------

def query_daily(data_type: str, start: date | None, end: date | None) -> list[dict]:
    sql = "SELECT day, value, unit FROM daily_metrics WHERE data_type=?"
    params: list[Any] = [data_type]
    if start:
        sql += " AND day >= ?"
        params.append(start.isoformat())
    if end:
        sql += " AND day <= ?"
        params.append(end.isoformat())
    sql += " ORDER BY day"
    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def query_daily_bulk() -> dict[str, list[dict]]:
    """Every type's daily series in one query — the dashboard's initial load."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT data_type, day, value, unit FROM daily_metrics ORDER BY data_type, day"
        ).fetchall()
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[r["data_type"]].append({"day": r["day"], "value": r["value"], "unit": r["unit"]})
    return dict(out)


def query_daily_raw(data_type: str) -> list[dict]:
    """(day, raw JSON) rows for a type — lets derived metrics re-parse stored points."""
    with _connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT day, raw FROM daily_metrics WHERE data_type=? ORDER BY day",
                (data_type,),
            ).fetchall()
        ]


def _local_day_utc_bound(d: date, *, end: bool = False) -> str:
    """The UTC instant (as a stored-format `...Z` string) where local calendar day `d`
    begins — or, with end=True, where the day AFTER it begins (an exclusive bound).

    Intraday `ts` are stored as RFC3339 UTC strings, but people, the coach's tools, and
    the chart widgets all think in *local* days. Slicing by date-prefix would select UTC
    days — a 6 PM-to-6 PM window at UTC-6 — making the rendered trace disagree with the
    local times narrated alongside it. Naive datetimes resolve via the system timezone,
    which is the user's timezone in this self-hosted, runs-on-your-own-machine app."""
    base = datetime.combine(d + timedelta(days=1) if end else d, datetime.min.time())
    return base.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def query_intraday(
    data_type: str,
    start: date | None,
    end: date | None,
    max_points: int = 1500,
) -> list[dict]:
    """Intraday series across *local* days [start, end], evenly downsampled to at most
    `max_points` points.

    Heart-rate alone is tens of thousands of points/day — sending them all is what made the
    dashboard crawl. We stride-sample in SQL (keep every Nth row, always the newest) so the
    payload and the chart stay light while the shape is preserved."""
    where = "WHERE data_type=?"
    params: list[Any] = [data_type]
    if start:
        where += " AND ts >= ?"
        params.append(_local_day_utc_bound(start))
    if end:
        where += " AND ts < ?"
        params.append(_local_day_utc_bound(end, end=True))
    with _connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM intraday_points {where}", params
        ).fetchone()[0]
        stride = max(1, -(-total // max_points))  # ceil(total / max_points)
        # Number rows newest-first so the most recent point always survives sampling,
        # keep every `stride`-th, then return oldest-first for the chart.
        sql = (
            "SELECT ts, value, unit FROM ("
            "  SELECT ts, value, unit,"
            "         ROW_NUMBER() OVER (ORDER BY ts DESC) AS rn"
            f"  FROM intraday_points {where}"
            ") WHERE (rn - 1) % ? = 0 ORDER BY ts"
        )
        return [dict(r) for r in conn.execute(sql, [*params, stride]).fetchall()]


def query_intraday_range(
    data_type: str,
    start_ts: str,
    end_ts: str,
    max_points: int = 600,
) -> list[dict]:
    """Intraday points inside an exact timestamp window (e.g. one workout session),
    evenly downsampled like query_intraday. Bounds are ISO UTC strings matching the
    stored `...Z` format, so plain string comparison is correct."""
    where = "WHERE data_type=? AND ts >= ? AND ts <= ?"
    params: list[Any] = [data_type, start_ts, end_ts]
    with _connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM intraday_points {where}", params
        ).fetchone()[0]
        stride = max(1, -(-total // max_points))
        sql = (
            "SELECT ts, value FROM ("
            "  SELECT ts, value,"
            "         ROW_NUMBER() OVER (ORDER BY ts DESC) AS rn"
            f"  FROM intraday_points {where}"
            ") WHERE (rn - 1) % ? = 0 ORDER BY ts"
        )
        return [dict(r) for r in conn.execute(sql, [*params, stride]).fetchall()]


# --- workout sessions ---------------------------------------------------------

def upsert_workouts(sessions: list[dict[str, Any]]) -> int:
    """Idempotently store parsed exercise sessions (keyed by the API point name)."""
    if not sessions:
        return 0
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO workout_sessions (id, day, start_ts, start_local, end_ts, activity, "
            "duration_min, calories, distance_km, steps, avg_hr, azm, raw) "
            "VALUES (:id, :day, :start_ts, :start_local, :end_ts, :activity, "
            ":duration_min, :calories, :distance_km, :steps, :avg_hr, :azm, :raw) "
            "ON CONFLICT(id) DO UPDATE SET day=excluded.day, start_ts=excluded.start_ts, "
            "start_local=excluded.start_local, end_ts=excluded.end_ts, activity=excluded.activity, "
            "duration_min=excluded.duration_min, calories=excluded.calories, "
            "distance_km=excluded.distance_km, steps=excluded.steps, avg_hr=excluded.avg_hr, "
            "azm=excluded.azm, raw=excluded.raw",
            sessions,
        )
    return len(sessions)


def get_workout(workout_id: str) -> dict | None:
    """One session with its raw timestamps (for the intraday drill-down)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, day, start_ts, start_local, end_ts, activity, duration_min, "
            "calories, distance_km, steps, avg_hr, azm FROM workout_sessions WHERE id=?",
            (workout_id,),
        ).fetchone()
    return dict(row) if row else None


def query_workouts(days: int | None = 90, limit: int = 200) -> list[dict]:
    """Individual sessions, newest first."""
    sql = ("SELECT id, day, start_local, activity, duration_min, calories, distance_km, "
           "steps, avg_hr, azm FROM workout_sessions")
    params: list[Any] = []
    if days:
        sql += " WHERE day >= ?"
        params.append((date.today() - timedelta(days=days)).isoformat())
    sql += " ORDER BY start_ts DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# --- sleep sessions -----------------------------------------------------------

def upsert_sleep_sessions(sessions: list[dict[str, Any]]) -> int:
    """Idempotently store parsed sleep sessions (keyed by the API point name)."""
    if not sessions:
        return 0
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO sleep_sessions (id, day, start_ts, start_local, end_ts, end_local, "
            "duration_min, raw) "
            "VALUES (:id, :day, :start_ts, :start_local, :end_ts, :end_local, "
            ":duration_min, :raw) "
            "ON CONFLICT(id) DO UPDATE SET day=excluded.day, start_ts=excluded.start_ts, "
            "start_local=excluded.start_local, end_ts=excluded.end_ts, "
            "end_local=excluded.end_local, duration_min=excluded.duration_min, "
            "raw=excluded.raw",
            sessions,
        )
    return len(sessions)


def query_sleep_sessions(start_day: str | None = None, end_day: str | None = None) -> list[dict]:
    """Sessions whose wake falls in [start_day, end_day], oldest first."""
    sql = ("SELECT id, day, start_ts, start_local, end_ts, end_local, duration_min "
           "FROM sleep_sessions")
    where, params = [], []
    if start_day:
        where.append("day >= ?"); params.append(start_day)
    if end_day:
        where.append("day <= ?"); params.append(end_day)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY day, start_ts"
    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# --- the daily schedule --------------------------------------------------------

_BLOCK_FIELDS = ("time", "label", "detail", "color", "remind", "active",
                 "days", "starts_on", "ends_on")
_ENTRY_FIELDS = ("time", "label", "detail", "done", "note")


def list_blocks(include_ended: bool = False) -> list[dict]:
    """Blocks ordered by time. Default: only current-and-upcoming (not yet ended);
    include_ended adds everything, for rendering past days as they were."""
    with _connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM schedule_blocks ORDER BY time, id").fetchall()]
    if include_ended:
        return rows
    today = date.today().isoformat()
    return [r for r in rows if not r["ends_on"] or r["ends_on"] >= today]


def get_block(block_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM schedule_blocks WHERE id=?", (block_id,)).fetchone()
    return dict(row) if row else None


def count_blocks() -> int:
    """All blocks ever created (archived included) — the seed-once check."""
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM schedule_blocks").fetchone()[0]


def add_block(time: str, label: str, detail: str | None = None,
              color: str = "neutral", remind: int = 1,
              days: str = "0123456", starts_on: str | None = None) -> dict:
    today = date.today().isoformat()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO schedule_blocks (time, label, detail, color, remind, days, "
            "starts_on, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time, label, detail, color, remind, days, starts_on or today, today),
        )
        bid = int(cur.lastrowid)
    return get_block(bid) or {}


def update_block(block_id: int, **fields: Any) -> dict | None:
    sets = {k: v for k, v in fields.items() if k in _BLOCK_FIELDS and v is not None}
    if sets:
        sets["updated_at"] = datetime.now(timezone.utc).isoformat()
        assign = ", ".join(f"{k}=?" for k in sets)
        with _connect() as conn:
            conn.execute(f"UPDATE schedule_blocks SET {assign} WHERE id=?",
                         (*sets.values(), block_id))
    return get_block(block_id)


def entries_between(start_day: str, end_day: str) -> list[dict]:
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM schedule_entries WHERE day >= ? AND day <= ? ORDER BY day, id",
            (start_day, end_day),
        ).fetchall()]


def get_entry(entry_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM schedule_entries WHERE id=?", (entry_id,)).fetchone()
    return dict(row) if row else None


def upsert_block_entry(day: str, block_id: int, **fields: Any) -> dict:
    """The (day, block) log row — created on first touch, partially updated after."""
    sets = {k: v for k, v in fields.items() if k in _ENTRY_FIELDS}
    sets["updated_at"] = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM schedule_entries WHERE day=? AND block_id=?", (day, block_id)
        ).fetchone()
        if row:
            assign = ", ".join(f"{k}=?" for k in sets)
            conn.execute(f"UPDATE schedule_entries SET {assign} WHERE id=?",
                         (*sets.values(), row["id"]))
            eid = int(row["id"])
        else:
            cols = ", ".join(sets)
            marks = ", ".join("?" for _ in sets)
            cur = conn.execute(
                f"INSERT INTO schedule_entries (day, block_id, {cols}) VALUES (?, ?, {marks})",
                (day, block_id, *sets.values()),
            )
            eid = int(cur.lastrowid)
    return get_entry(eid) or {}


def add_oneoff_entry(day: str, time: str, label: str, detail: str | None = None) -> dict:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO schedule_entries (day, block_id, time, label, detail, updated_at) "
            "VALUES (?, NULL, ?, ?, ?, ?)",
            (day, time, label, detail, datetime.now(timezone.utc).isoformat()),
        )
        eid = int(cur.lastrowid)
    return get_entry(eid) or {}


def update_entry(entry_id: int, **fields: Any) -> dict | None:
    sets = {k: v for k, v in fields.items() if k in _ENTRY_FIELDS}
    if sets:
        sets["updated_at"] = datetime.now(timezone.utc).isoformat()
        assign = ", ".join(f"{k}=?" for k in sets)
        with _connect() as conn:
            conn.execute(f"UPDATE schedule_entries SET {assign} WHERE id=?",
                         (*sets.values(), entry_id))
    return get_entry(entry_id)


def delete_entry(entry_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM schedule_entries WHERE id=?", (entry_id,))


# --- web-push subscriptions ----------------------------------------------------

def add_push_subscription(endpoint: str, subscription_json: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO push_subscriptions (endpoint, subscription, created_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(endpoint) DO UPDATE SET subscription=excluded.subscription",
            (endpoint, subscription_json, datetime.now(timezone.utc).isoformat()),
        )


def list_push_subscriptions() -> list[dict]:
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT endpoint, subscription, created_at, last_ok FROM push_subscriptions"
        ).fetchall()]


def touch_push_subscription(endpoint: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE push_subscriptions SET last_ok=? WHERE endpoint=?",
                     (datetime.now(timezone.utc).isoformat(), endpoint))


def delete_push_subscription(endpoint: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
        return cur.rowcount > 0


# --- coach memory -------------------------------------------------------------

def add_memory(content: str, category: str = "other") -> int:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO coach_memory (created_at, updated_at, category, content) "
            "VALUES (?, ?, ?, ?)",
            (now, now, category, content),
        )
        return int(cur.lastrowid)


def list_memories(include_inactive: bool = False) -> list[dict]:
    sql = ("SELECT id, created_at, updated_at, category, content, active FROM coach_memory")
    if not include_inactive:
        sql += " WHERE active=1"
    sql += " ORDER BY id"
    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def forget_memory(memory_id: int) -> bool:
    """Soft-deactivate. Returns False when no active memory had that id."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE coach_memory SET active=0, updated_at=? WHERE id=? AND active=1",
            (datetime.now(timezone.utc).isoformat(), memory_id),
        )
        return cur.rowcount > 0


# --- goals ------------------------------------------------------------------

def list_goals() -> list[dict]:
    with _connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, data_type, comparator, target, created_at FROM goals "
                "WHERE active=1 ORDER BY id"
            ).fetchall()
        ]


def count_goals() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0]


def add_goal(data_type: str, comparator: str, target: float) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO goals (data_type, comparator, target, created_at) VALUES (?, ?, ?, ?)",
            (data_type, comparator, target, datetime.now(timezone.utc).isoformat()),
        )
        return int(cur.lastrowid)


def update_goal(goal_id: int, target: float | None = None, comparator: str | None = None) -> None:
    sets, params = [], []
    if target is not None:
        sets.append("target=?"); params.append(target)
    if comparator is not None:
        sets.append("comparator=?"); params.append(comparator)
    if not sets:
        return
    params.append(goal_id)
    with _connect() as conn:
        conn.execute(f"UPDATE goals SET {', '.join(sets)} WHERE id=?", params)


def delete_goal(goal_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM goals WHERE id=?", (goal_id,))


def has_any_data() -> bool:
    """Whether any daily metric has ever landed — false means brand-new install."""
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM daily_metrics LIMIT 1").fetchone() is not None


def sync_status() -> list[dict]:
    with _connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT data_type, kind, last_day, last_sync_at FROM sync_state "
                "ORDER BY data_type, kind"
            ).fetchall()
        ]
