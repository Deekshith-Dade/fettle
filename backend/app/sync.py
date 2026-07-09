"""Incremental sync engine.

For each registered data type we keep a per-(type, kind) watermark — the last day
already stored. A run fetches only [watermark+1, today], so repeated syncs are cheap.
First-ever run backfills `initial_backfill_days`.

Daily rollups are pulled for everything; intraday detail only for the types where it's
worthwhile (steps/HR/etc.), controlled by `DataType.supports_intraday`.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import readiness, store
from .auth import load_credentials
from .config import REGISTRY, REGISTRY_BY_NAME, DataType, settings
from .health_client import HealthClient


@dataclass
class TypeResult:
    data_type: str
    daily_rows: int = 0
    intraday_rows: int = 0
    error: str | None = None


@dataclass
class SyncReport:
    results: list[TypeResult] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(r.daily_rows + r.intraday_rows for r in self.results)

    @property
    def ok(self) -> bool:
        return all(r.error is None for r in self.results)


def _window(data_type: str, kind: str, today: date) -> tuple[date, date]:
    """[start, end_exclusive) to fetch. Re-fetches the last watermark day to catch
    late-arriving samples."""
    wm = store.get_watermark(data_type, kind)
    backfill = (
        settings.initial_backfill_days if kind == "daily"
        else settings.initial_intraday_days
    )
    start = wm if wm else today - timedelta(days=backfill)
    end = today + timedelta(days=1)  # include today (end is exclusive)
    return start, end


def _sync_one(client: HealthClient, dt: DataType, today: date) -> TypeResult:
    result = TypeResult(data_type=dt.api_name)
    try:
        if dt.daily_via_list:
            # One point/day via `list`; fetch the whole (small) history and store as daily.
            points = list(client.list_all(dt.api_name))
            result.daily_rows = store.upsert_daily(
                dt.api_name, dt.unit, points, scale=dt.value_scale,
                sum_fields=dt.sum_fields, diff_fields=dt.diff_fields)
            store.set_watermark(dt.api_name, "daily", today)
            return result

        if dt.supports_daily_rollup:
            start, end = _window(dt.api_name, "daily", today)
            points = list(client.daily_rollup(dt, start, end))
            result.daily_rows = store.upsert_daily(
                dt.api_name, dt.unit, points, scale=dt.value_scale,
                sum_fields=dt.sum_fields, diff_fields=dt.diff_fields)
            store.set_watermark(dt.api_name, "daily", today)

        if dt.supports_intraday:
            start, end = _window(dt.api_name, "intraday", today)
            # Stream straight into batched upserts — heart-rate alone can be ~50k points/day.
            result.intraday_rows = store.upsert_intraday(
                dt.api_name, dt.unit, client.list_intraday(dt, start, end))
            store.set_watermark(dt.api_name, "intraday", today)
    except Exception as exc:  # one type failing shouldn't abort the whole run
        result.error = f"{type(exc).__name__}: {exc}"
    return result


# --- session processors (sleep, exercise) --------------------------------------

def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _offset_seconds(s: str | None) -> int:
    return int(s.rstrip("s")) if s else 0  # e.g. "-21600s" -> -21600


def _local_day(utc_iso: str, offset_iso: str | None) -> str:
    """Civil (local) date for a UTC timestamp + its UTC offset."""
    return (_parse_ts(utc_iso) + timedelta(seconds=_offset_seconds(offset_iso))).date().isoformat()


def _duration_s(a: str | None, b: str | None) -> float:
    if not (a and b):
        return 0.0
    return (_parse_ts(b) - _parse_ts(a)).total_seconds()


def _sync_sleep(client: HealthClient, today: date) -> TypeResult:
    """Derive daily sleep metrics (duration, per-stage time, efficiency) from `sleep`
    session points. Each point is a sleep session with a `stages` breakdown."""
    result = TypeResult(data_type="sleep")
    try:
        agg: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for p in client.list_all("sleep"):
            s = p.get("sleep", {})
            iv = s.get("interval", {})
            if not iv.get("endTime"):
                continue
            day = _local_day(iv["endTime"], iv.get("endUtcOffset"))  # Fitbit dates to wake day
            per: dict[str, float] = defaultdict(float)
            for st in s.get("stages") or []:
                per[st.get("type", "?")] += _duration_s(st.get("startTime"), st.get("endTime"))
            asleep = per["LIGHT"] + per["DEEP"] + per["REM"]
            in_bed = _duration_s(iv.get("startTime"), iv.get("endTime")) or (asleep + per["AWAKE"])
            a = agg[day]
            a["sleep-duration"] += asleep
            a["sleep-rem"] += per["REM"]
            a["sleep-deep"] += per["DEEP"]
            a["sleep-light"] += per["LIGHT"]
            a["sleep-awake"] += per["AWAKE"]
            a["_asleep"] += asleep
            a["_in_bed"] += in_bed
        H = 3600.0
        rows = 0
        for metric in ("sleep-duration", "sleep-rem", "sleep-deep", "sleep-light", "sleep-awake"):
            rows += store.upsert_daily_values(metric, "h", {d: agg[d][metric] / H for d in agg})
        eff = {d: agg[d]["_asleep"] / agg[d]["_in_bed"] * 100 for d in agg if agg[d]["_in_bed"] > 0}
        rows += store.upsert_daily_values("sleep-efficiency", "%", eff)
        rows += store.upsert_daily_values("sleep-score", "", _sleep_scores(agg, eff))
        result.daily_rows = rows
        store.set_watermark("sleep", "daily", today)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    return result


def _sleep_scores(agg: dict[str, dict[str, float]], eff: dict[str, float]) -> dict[str, int]:
    """0-100 nightly sleep score, mirroring the app's Duration/Quality/Restoration split.

    Duration (50): full marks inside the 8-9h band, sliding off over ±4h outside it.
    Quality (25): deep+REM share of sleep vs a 40% target.
    Restoration (25): efficiency vs a 95% target (proxy for restlessness/sleeping HR).
    The formula is our own transparent heuristic, not Fitbit's proprietary model.
    """
    clamp01 = lambda x: max(0.0, min(1.0, x))  # noqa: E731
    scores: dict[str, int] = {}
    for d, a in agg.items():
        asleep_s = a["_asleep"]
        if asleep_s <= 0:
            continue
        dur_h = asleep_s / 3600.0
        duration = clamp01(1 - max(0.0, 8 - dur_h) / 4 - max(0.0, dur_h - 9) / 4)
        quality = clamp01(((a["sleep-deep"] + a["sleep-rem"]) / asleep_s) / 0.40)
        restoration = clamp01(eff.get(d, 90.0) / 95.0)
        scores[d] = round(50 * duration + 25 * quality + 25 * restoration)
    return scores


def _derive_cardio_load(today: date) -> TypeResult:
    """TRIMP-style daily training load from the per-zone minutes already stored in the
    active-zone-minutes raw points: 1·fat-burn + 2·cardio + 3·peak. Pure re-derivation
    from the DB — no API call."""
    result = TypeResult(data_type="cardio-load")
    try:
        loads: dict[str, float] = {}
        for r in store.query_daily_raw("active-zone-minutes"):
            zones = json.loads(r["raw"]).get("activeZoneMinutes")
            if not isinstance(zones, dict):
                continue
            grab = lambda k: float(zones.get(k) or 0)  # noqa: E731
            loads[r["day"]] = (grab("sumInFatBurnHeartZone")
                               + 2 * grab("sumInCardioHeartZone")
                               + 3 * grab("sumInPeakHeartZone"))
        result.daily_rows = store.upsert_daily_values("cardio-load", "", loads)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    return result


def _sync_exercise(client: HealthClient, today: date) -> TypeResult:
    """Derive daily workout metrics (time, count, distance, calories) from `exercise`
    session points, each carrying a `metricsSummary`."""
    result = TypeResult(data_type="exercise")
    try:
        agg: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for p in client.list_all("exercise"):
            ex = p.get("exercise", {})
            iv = ex.get("interval", {})
            if not iv.get("startTime"):
                continue
            day = _local_day(iv["startTime"], iv.get("startUtcOffset"))
            ms = ex.get("metricsSummary", {})
            a = agg[day]
            a["exercise-minutes"] += _duration_s(iv.get("startTime"), iv.get("endTime")) / 60.0
            a["exercise-count"] += 1
            a["exercise-calories"] += float(ms.get("caloriesKcal") or 0)
            a["exercise-distance"] += float(ms.get("distanceMillimeters") or 0) / 1e6  # mm -> km
        rows = 0
        for metric, unit in (
            ("exercise-minutes", "min"), ("exercise-count", "sessions"),
            ("exercise-distance", "km"), ("exercise-calories", "kcal"),
        ):
            rows += store.upsert_daily_values(metric, unit, {d: agg[d][metric] for d in agg})
        result.daily_rows = rows
        store.set_watermark("exercise", "daily", today)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    return result


def run_sync(data_types: list[DataType] | None = None, today: date | None = None) -> SyncReport:
    """Sync the given data types (default: the whole registry)."""
    store.init_db()
    creds = load_credentials()  # raises TokenExpiredError -> caller tells user to re-auth
    client = HealthClient(creds)
    today = today or date.today()
    types = data_types or REGISTRY

    report = SyncReport()
    for dt in types:
        if dt.derived:
            continue  # produced by the session processors below, not fetched
        report.results.append(_sync_one(client, dt, today))

    # Run a session processor when any of its derived outputs is in scope (default sync
    # includes them all; targeted syncs of e.g. "sleep-rem" also trigger it).
    names = {dt.api_name for dt in types}
    if any(n.startswith("sleep-") for n in names):
        report.results.append(_sync_sleep(client, today))
    if any(n.startswith("exercise-") for n in names):
        report.results.append(_sync_exercise(client, today))
    if "cardio-load" in names:
        report.results.append(_derive_cardio_load(today))

    # Readiness derives from the freshly-synced daily metrics, so compute it last.
    try:
        report.results.append(TypeResult("readiness", daily_rows=readiness.recompute()))
    except Exception as exc:
        report.results.append(TypeResult("readiness", error=f"{type(exc).__name__}: {exc}"))
    return report


def resolve_types(names: list[str] | None) -> list[DataType]:
    """Map CLI/API names to registry entries; None -> all."""
    if not names:
        return REGISTRY
    out = []
    for n in names:
        dt = REGISTRY_BY_NAME.get(n)
        if not dt:
            raise ValueError(f"Unknown data type '{n}'. Known: {list(REGISTRY_BY_NAME)}")
        out.append(dt)
    return out
