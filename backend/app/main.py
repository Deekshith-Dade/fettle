"""FastAPI app: OAuth callback, sync trigger, and read endpoints for the dashboard."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from . import (
    auth, benchmarks, briefing, chat, coach, config, goals, insights, readiness,
    sleep_analysis, store, sync, workouts,
)
from .config import REGISTRY, REGISTRY_BY_NAME, settings

# Ensure the schema exists as soon as the module is imported (covers TestClient,
# workers, and any code path that touches the DB before a request arrives).
store.init_db()


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    yield


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
