"""FastAPI app: OAuth callback, sync trigger, and read endpoints for the dashboard."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel

from . import (
    auth, benchmarks, briefing, chat, coach, config, goals, insights, lights, push,
    readiness, schedule, sleep_analysis, store, strain, sync, vital_age, workouts,
)
from .config import REGISTRY, REGISTRY_BY_NAME, settings

# Ensure the schema exists as soon as the module is imported (covers TestClient,
# workers, and any code path that touches the DB before a request arrives).
store.init_db()
schedule.ensure_seed()


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    store.init_db()
    schedule.ensure_seed()
    stop = asyncio.Event()
    engine = asyncio.create_task(lights.run_engine(stop))
    yield
    stop.set()
    await engine


app = FastAPI(title="fettle", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(chat.router)  # AI coach: /api/chat*


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "authenticated": auth.has_valid_token(),
        # Days until the Testing-mode refresh token dies (None until the next auth
        # records its consent timestamp).
        "token_days_left": auth.token_days_left(),
    }


@app.get("/api/data-types")
def data_types() -> list[dict]:
    """The registry — what the dashboard can chart."""
    return [
        {
            "name": dt.api_name,
            "label": dt.label,
            "unit": dt.unit,
            "scope": dt.scope.value,
            # daily-summary and derived types have no sub-daily stream, so don't advertise
            # an intraday view for them (it would render an empty section).
            "intraday": dt.supports_intraday and not dt.daily_via_list and not dt.derived,
            "group": config.group_for(dt.api_name),
        }
        for dt in REGISTRY
    ]


# --- auth --------------------------------------------------------------------

@app.get("/auth/login")
def login() -> RedirectResponse:
    url, _state = auth.build_authorization_url()
    return RedirectResponse(url)


@app.get("/auth/callback", response_class=HTMLResponse)
def auth_callback(request: Request) -> HTMLResponse:
    # The full request URL carries the ?code=&state= that the code exchange needs.
    # Browser-facing endpoint: render HTML for both outcomes, not JSON.
    try:
        auth.exchange_code(str(request.url))
    except Exception as exc:  # covers AuthError + oauthlib denials (user hit Cancel)
        return HTMLResponse(
            "<h2>fettle — connection failed</h2>"
            f"<p>{exc}</p>"
            f'<p><a href="{settings.frontend_url}">Back to fettle</a> and try again.</p>',
            status_code=400,
        )
    # Land back in the app. A meta-refresh instead of a 307 so that if the frontend
    # isn't running the user still sees a success page (the token IS stored).
    return HTMLResponse(
        f'<meta http-equiv="refresh" content="0;url={settings.frontend_url}/?connected=1">'
        "<h2>fettle connected ✅</h2>"
        f'<p>Token stored. <a href="{settings.frontend_url}/?connected=1">Open fettle</a>.</p>'
    )


# --- first-run setup ------------------------------------------------------------

@app.get("/api/setup/status")
def setup_status() -> dict:
    """Everything the first-run wizard needs to render its checklist."""
    return {
        "credentials": auth.client_credentials_info(),
        "authenticated": auth.has_valid_token(),
        "token_days_left": auth.token_days_left(),
        "has_data": store.has_any_data(),
        "redirect_uri": settings.oauth_redirect_uri,
        "scopes": config.AUTH_SCOPES,
    }


class CredentialsIn(BaseModel):
    json_text: str


@app.post("/api/setup/credentials")
def setup_credentials(body: CredentialsIn) -> dict:
    try:
        info, warnings = auth.save_client_credentials(body.json_text)
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "credentials": info, "warnings": warnings}


# --- sync --------------------------------------------------------------------

@app.post("/api/sync")
def trigger_sync(types: list[str] | None = Query(default=None)) -> dict:
    try:
        selected = sync.resolve_types(types)
        report = sync.run_sync(selected)
    except auth.TokenExpiredError as exc:
        raise HTTPException(401, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {
        "ok": report.ok,
        "total_rows": report.total_rows,
        "results": [vars(r) for r in report.results],
    }


@app.get("/api/sync/status")
def sync_state() -> list[dict]:
    return store.sync_status()


@app.get("/api/readiness")
def readiness_today() -> dict:
    """Latest readiness score + transparent component breakdown for the hero."""
    data = readiness.today_breakdown()
    if data is None:
        raise HTTPException(404, "Not enough data to compute readiness yet.")
    return data


@app.get("/api/insights")
def insights_feed(limit: int = Query(default=8, ge=1, le=20)) -> dict:
    """Ranked plain-English observations derived from the stored series."""
    return {"insights": insights.compute(limit=limit)}


@app.get("/api/briefing")
def briefing_latest() -> dict:
    """The stored LLM daily briefing — instant read, generated post-sync/on demand."""
    return {"briefing": briefing.latest()}


@app.post("/api/briefing/refresh")
def briefing_refresh() -> dict:
    """Regenerate the briefing now (sync def → runs in the threadpool; takes ~20-60s)."""
    try:
        return {"briefing": briefing.generate(force=True)}
    except briefing.BriefingError as exc:
        raise HTTPException(502, f"Briefing generation failed: {exc}")


@app.get("/api/briefing/weekly")
def briefing_weekly() -> dict:
    """The stored weekly retrospective (regenerated Sundays post-sync, or on demand)."""
    return {"briefing": briefing.latest("weekly")}


@app.post("/api/briefing/weekly/refresh")
def briefing_weekly_refresh() -> dict:
    try:
        return {"briefing": briefing.generate_weekly(force=True)}
    except briefing.BriefingError as exc:
        raise HTTPException(502, f"Weekly retrospective failed: {exc}")


# --- coach memory (what the chat coach has been told and kept) -----------------

@app.get("/api/coach/memory")
def coach_memory_list() -> dict:
    """Durable facts the coach saved from chat — full transparency into what it knows."""
    return {"memories": store.list_memories()}


@app.delete("/api/coach/memory/{memory_id}")
def coach_memory_delete(memory_id: int) -> dict:
    if not store.forget_memory(memory_id):
        raise HTTPException(404, f"No active memory with id {memory_id}.")
    return {"ok": True}


@app.get("/api/coach")
def coach_today(limit: int = Query(default=3, ge=1, le=5)) -> dict:
    """Ranked 'what to do today' recommendations synthesised from the current state."""
    return coach.recommend(limit=limit)


@app.get("/api/benchmarks")
def benchmarks_standing() -> dict:
    """Where the user's habitual values stand against evidence-based reference norms."""
    return benchmarks.evaluate_all()


@app.get("/api/sleep/detail")
def sleep_detail() -> dict:
    """Deep-dive on sleep: stage mix vs targets, debt, regularity, and trend."""
    data = sleep_analysis.detail()
    if data is None:
        raise HTTPException(404, "Not enough sleep data yet.")
    return data


@app.get("/api/rings")
def rings() -> dict:
    """The daily-snapshot trio (Strain / Recovery / Sleep), each 0-100, for the rings card.
    Recovery = readiness, Sleep = last night's sleep score, Strain = today's cardio load on
    the personal scale (+ its recovery-derived optimal band). Missing pieces come back null."""
    rec = readiness.today_breakdown()
    sd = sleep_analysis.detail()
    ln = sd["last_night"] if sd else None
    st = strain.today()
    return {
        "as_of": date.today().isoformat(),
        "rings": [
            {
                "key": "strain", "label": "Strain",
                "value": st["score"] if st else None,
                "day": st["day"] if st else None,
                "target": st["target"] if st else None,
                "detail": st["detail"] if st else "No training-load history yet.",
            },
            {
                "key": "recovery", "label": "Recovery",
                "value": rec["score"] if rec else None,
                "day": rec["date"] if rec else None,
                "detail": rec["narrative"] if rec else "Not enough data to score recovery yet.",
            },
            {
                "key": "sleep", "label": "Sleep",
                "value": round(ln["score"]) if ln and ln.get("score") is not None else None,
                "day": ln["day"] if ln else None,
                "detail": (f"{ln['duration']:.1f} h · {ln['efficiency']:.0f}% efficient"
                           if ln and ln.get("score") is not None else "No sleep score yet."),
            },
        ],
    }


@app.get("/api/vital-age")
def vital_age_endpoint() -> dict:
    """Effective ('vital') age from age-referenced norms — WHOOP/Bevel-style, transparent."""
    data = vital_age.compute()
    if data is None:
        raise HTTPException(404, "Not enough data to compute Vital Age yet.")
    return data


# --- the daily schedule --------------------------------------------------------

class BlockIn(BaseModel):
    time: str
    label: str
    detail: str | None = None
    color: str = "neutral"
    remind: bool = True
    days: str = "0123456"        # weekday digits, Mon=0…Sun=6
    starts_on: str | None = None  # ISO; default today


class BlockPatch(BaseModel):
    time: str | None = None
    label: str | None = None
    detail: str | None = None
    color: str | None = None
    remind: bool | None = None
    days: str | None = None
    starts_on: str | None = None
    apply_from: str | None = None  # ISO: 'this and following days' — splits the block


class ScheduleLogIn(BaseModel):
    day: str | None = None       # ISO; default today
    block_id: int | None = None  # template block …
    entry_id: int | None = None  # … or a one-off item
    done: bool | None = None
    note: str | None = None


class OneOffIn(BaseModel):
    day: str
    time: str
    label: str
    detail: str | None = None


def _sched_day(s: str | None) -> date:
    if s is None:
        return date.today()
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, f"Invalid day '{s}' — use ISO YYYY-MM-DD.")


@app.get("/api/schedule/blocks")
def schedule_blocks() -> dict:
    """The editable template."""
    return {"blocks": store.list_blocks()}


@app.post("/api/schedule/blocks")
def schedule_block_create(body: BlockIn) -> dict:
    try:
        return {"ok": True, "block": schedule.create_block(
            body.time, body.label, body.detail, body.color, body.remind,
            body.days, body.starts_on)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.patch("/api/schedule/blocks/{block_id}")
def schedule_block_edit(block_id: int, body: BlockPatch) -> dict:
    """Edit everywhere, or — with apply_from — from that day onward (the old
    version ends the day before; past days keep it)."""
    fields = dict(time=body.time, label=body.label, detail=body.detail,
                  color=body.color, remind=body.remind, days=body.days,
                  starts_on=body.starts_on)
    try:
        if body.apply_from:
            block = schedule.edit_block_from(block_id, _sched_day(body.apply_from), **fields)
        else:
            block = schedule.edit_block(block_id, **fields)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "block": block}


@app.delete("/api/schedule/blocks/{block_id}")
def schedule_block_remove(block_id: int, from_day: str | None = Query(default=None, alias="from")) -> dict:
    """Remove from `from` (default today) onward — never deletes; earlier days
    keep rendering the block as it was."""
    try:
        schedule.end_block(block_id, _sched_day(from_day))
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True}


@app.get("/api/schedule/day")
def schedule_day(day: str | None = None) -> dict:
    """One day's timeline: blocks + log state + one-offs + synced-context chips."""
    return schedule.day_view(_sched_day(day))


@app.get("/api/schedule/month")
def schedule_month(month: str = Query(default="")) -> dict:
    """Calendar rollup for 'YYYY-MM' (default: this month) + per-block adherence."""
    try:
        y, m = (int(p) for p in (month or date.today().strftime("%Y-%m")).split("-"))
        date(y, m, 1)
    except (ValueError, TypeError):
        raise HTTPException(400, f"Invalid month '{month}' — use YYYY-MM.")
    return schedule.month_view(y, m)


@app.post("/api/schedule/log")
def schedule_log(body: ScheduleLogIn) -> dict:
    """Mark a block done/missed and/or note it. Partial — only sent fields change."""
    try:
        schedule.log(_sched_day(body.day), body.block_id, body.entry_id,
                     body.done, body.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "day": schedule.day_view(_sched_day(body.day))}


@app.post("/api/schedule/oneoff")
def schedule_oneoff(body: OneOffIn) -> dict:
    """Add a one-off item to a single day."""
    try:
        item = schedule.add_oneoff(_sched_day(body.day), body.time, body.label, body.detail)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "entry": item, "day": schedule.day_view(_sched_day(body.day))}


@app.delete("/api/schedule/entries/{entry_id}")
def schedule_entry_delete(entry_id: int) -> dict:
    try:
        schedule.remove_oneoff(entry_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


# --- circadian lighting --------------------------------------------------------

class LightOverrideIn(BaseModel):
    mode: str                 # focus | movie | warm | off
    minutes: int = 60


class LightSetIn(BaseModel):
    on: bool = True
    level_pct: int | None = None    # 1..100
    kelvin: int | None = None       # 2200..6500


@app.get("/api/lights")
async def lights_status() -> dict:
    """Live lamp state + today's planned curve + the last 24h of transitions."""
    return await lights.status_now()


@app.post("/api/lights/override")
async def lights_override(body: LightOverrideIn) -> dict:
    try:
        info = lights.set_override(body.mode, body.minutes)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    await lights.tick()  # take effect now, not at the next 30s tick
    return {"ok": True, **info}


@app.delete("/api/lights/override")
async def lights_override_clear() -> dict:
    """Drop override, holdoff, AND any journey — hand the lamp back to the curve."""
    lights.clear_override()
    await lights.tick()
    return {"ok": True}


class JourneyIn(BaseModel):
    kind: str = "nap"
    minutes: int = 60


@app.post("/api/lights/journey")
async def lights_journey(body: JourneyIn) -> dict:
    """Start a timed light arc (nap: fade → dark → gentle amber wake ramp)."""
    try:
        j = lights.start_journey(body.kind, body.minutes)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    await lights.tick()  # begin the fade now
    return {"ok": True, "journey": j}


@app.post("/api/lights/set")
async def lights_set(body: LightSetIn) -> dict:
    """One-shot manual set from the dashboard; the engine holds off for 2h after."""
    from datetime import datetime, timedelta

    from .circadian import LightTarget
    level = round((body.level_pct or 50) * 2.54)
    mireds = round(1_000_000 / body.kelvin) if body.kelvin else 370
    tgt = LightTarget(body.on, max(1, min(254, level)),
                      max(154, min(455, mireds)), "api-set", 5)
    if not await lights.apply(tgt, source="api"):
        raise HTTPException(502, "Lamp unreachable — is the Matter sidecar up?")
    store.light_state_save(holdoff_until=(datetime.now() + timedelta(hours=2))
                           .isoformat(timespec="seconds"))
    return {"ok": True, "applied": tgt.as_dict()}


# --- web push (phone reminders) ------------------------------------------------

class PushSubscribeIn(BaseModel):
    subscription: dict


class PushUnsubscribeIn(BaseModel):
    endpoint: str


@app.get("/api/push/key")
def push_key() -> dict:
    """The VAPID public key the browser subscribes with, plus how many devices are on."""
    return {"key": push.public_key(), "subscriptions": push.subscription_count()}


@app.post("/api/push/subscribe")
def push_subscribe(body: PushSubscribeIn) -> dict:
    try:
        push.subscribe(body.subscription)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "subscriptions": push.subscription_count()}


@app.post("/api/push/unsubscribe")
def push_unsubscribe(body: PushUnsubscribeIn) -> dict:
    return {"ok": push.unsubscribe(body.endpoint),
            "subscriptions": push.subscription_count()}


@app.post("/api/push/test")
def push_test() -> dict:
    """Send a test push to every subscribed device — the 'did it work?' button."""
    n = push.send_all("fettle", "Push reminders are working on this device.", ttl=300)
    return {"delivered": n}


# --- home-screen icons -------------------------------------------------------

# iOS builds webclip tiles with a system fetcher that rejects the proxy's mkcert
# cert and can't be trusted to resolve ts.net names, so both apps' icon links
# point here: plain http on the tailscale IP — the one fetch pattern the phone
# has demonstrably completed (pre-proxy installs got their icons this way) — and
# uvicorn's access log makes every attempt visible. Tally's icons ride along
# because this is the only request-logged server on the Mac; webclips bake the
# icon at add-time, so the cross-app URL never matters after install.
_ICONS_DIR = Path(__file__).resolve().parent.parent / "icons"


@app.api_route("/api/icon/{name}", methods=["GET", "HEAD"])
def app_icon(name: str) -> FileResponse:
    path = _ICONS_DIR / name
    if not path.is_file() or path.parent != _ICONS_DIR or path.suffix != ".png":
        raise HTTPException(status_code=404, detail="no such icon")
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})


# --- goals -------------------------------------------------------------------

class GoalIn(BaseModel):
    data_type: str
    comparator: str  # 'gte' (at least) | 'lte' (at most)
    target: float


class GoalPatch(BaseModel):
    target: float | None = None
    comparator: str | None = None


@app.get("/api/goals")
def goals_list() -> dict:
    """Every active goal scored against the data, plus the aggregate rollup."""
    return goals.evaluate_all()


@app.post("/api/goals")
def goals_create(goal: GoalIn) -> dict:
    if goal.comparator not in goals.COMPARATORS:
        raise HTTPException(400, "comparator must be 'gte' or 'lte'.")
    if goal.data_type not in REGISTRY_BY_NAME:
        raise HTTPException(404, f"Unknown metric '{goal.data_type}'.")
    gid = store.add_goal(goal.data_type, goal.comparator, goal.target)
    return {"id": gid}


@app.patch("/api/goals/{goal_id}")
def goals_update(goal_id: int, patch: GoalPatch) -> dict:
    if patch.comparator is not None and patch.comparator not in goals.COMPARATORS:
        raise HTTPException(400, "comparator must be 'gte' or 'lte'.")
    store.update_goal(goal_id, target=patch.target, comparator=patch.comparator)
    return {"ok": True}


@app.delete("/api/goals/{goal_id}")
def goals_delete(goal_id: int) -> dict:
    store.delete_goal(goal_id)
    return {"ok": True}


# --- data --------------------------------------------------------------------

@app.get("/api/data/daily")
def daily_bulk() -> dict:
    """Every type's daily series in one response — the dashboard's initial load."""
    return {"series": store.query_daily_bulk()}


@app.get("/api/workouts")
def workouts_list(days: int = Query(default=90, ge=1, le=365)) -> dict:
    """Individual exercise sessions, newest first (the exercise-* dailies aggregate these)."""
    return {"workouts": store.query_workouts(days=days)}


@app.get("/api/workouts/detail")
def workout_detail(id: str = Query(...)) -> dict:
    """One session with its intraday heart-rate trace and time-in-zone. The id is the
    API dataPoint name (contains slashes), hence a query param rather than a path part."""
    data = workouts.detail(id)
    if not data:
        raise HTTPException(404, "No such workout session.")
    return data


def _require_type(name: str):
    dt = REGISTRY_BY_NAME.get(name)
    if not dt:
        raise HTTPException(404, f"Unknown data type '{name}'.")
    return dt


@app.get("/api/data/{data_type}/daily")
def daily(
    data_type: str,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    dt = _require_type(data_type)
    return {
        "data_type": data_type,
        "label": dt.label,
        "unit": dt.unit,
        "points": store.query_daily(data_type, start, end),
    }


@app.get("/api/data/{data_type}/intraday")
def intraday(
    data_type: str,
    start: date | None = None,
    end: date | None = None,
    max_points: int = Query(default=1500, ge=100, le=20000),
) -> dict:
    dt = _require_type(data_type)
    if not dt.supports_intraday:
        raise HTTPException(400, f"{data_type} has no intraday data.")
    return {
        "data_type": data_type,
        "label": dt.label,
        "unit": dt.unit,
        "points": store.query_intraday(data_type, start, end, max_points=max_points),
    }
