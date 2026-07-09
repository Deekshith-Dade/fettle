# fitbit-plus

Pull your own Fitbit / Pixel health data from the **Google Health API** into a local
SQLite store, then explore it through a FastAPI backend and a Next.js dashboard —
with more granularity than the Fitbit app exposes.

> The Google Health API replaces the legacy Fitbit Web API (full shutdown Sept 2026).
> This project is built for **personal, single-user** use: it runs your OAuth consent
> screen in *Testing* mode with yourself as the only test user, so no third-party
> security review is required.

## Architecture

```
backend/                Python 3.11+
  app/
    config.py           Settings + the data-type registry (what to sync)
    auth.py             Google OAuth flow, token storage, auto-refresh
    health_client.py    Thin client over the Health API (list + dailyRollUp)
    store.py            SQLite schema, upserts, query helpers
    sync.py             Incremental sync engine (per-type date watermarks)
    main.py             FastAPI app (auth, sync, data endpoints)
  cli.py                `auth` and `sync` commands
frontend/               Next.js dashboard (reads the FastAPI API)
```

## One-time setup

1. **Migrate your Fitbit account to a Google account** (mandatory by 2026-05-19 anyway).
2. In [Google Cloud Console](https://console.cloud.google.com):
   - Create a project and **enable the Google Health API**.
   - Configure the **OAuth consent screen**: User type *External*, publishing status
     left at **Testing**, and add your own email under **Test users**.
   - Create an **OAuth client ID** (type *Web application*) with redirect URI
     `http://localhost:8400/auth/callback` (must match `oauth_redirect_uri` in
     `backend/.env`). Download the JSON.
3. Save the downloaded file as `backend/credentials.json`.

> ⚠️ **Testing-mode caveat:** refresh tokens expire after **7 days**. The sync detects
> this and tells you to re-run `auth`. That's the trade-off for skipping the security
> review — fine for a personal archive you sync manually or weekly.

## Quick start

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python cli.py auth          # opens browser, stores token.json
python cli.py sync          # pulls data into health.db
# `--host ::` binds IPv4 + IPv6 (dual-stack). Without it uvicorn is IPv4-only, and
# Safari — which resolves `localhost` to IPv6 ::1 first — can't reach the API, so the
# dashboard loads but never fills in. Chrome falls back to IPv4, hiding the problem.
uvicorn app.main:app --reload --host :: --port 8400   # API at http://localhost:8400

# Frontend (separate terminal)
cd frontend
npm install
npm run dev -- -p 3400      # dashboard at http://localhost:3400
```

(Ports are non-default — 8400/3400 — configured via `backend/.env` and
`frontend/.env.local`.)

## Derived metrics

Beyond the raw Google Health data types, the sync computes its own transparent
Premium-style metrics (all in `backend/app/`):

- **Readiness** (`readiness.py`) — 0-100 recovery index from HRV, resting HR, sleep,
  training load, and skin-temp deviation vs your own 28-day baseline.
- **Sleep Score** (`sync._sleep_scores`) — 0-100 per night: duration (50) +
  deep/REM share (25) + efficiency (25).
- **Cardio Load** (`sync._derive_cardio_load`) — TRIMP-style daily training load:
  1·fat-burn + 2·cardio + 3·peak zone minutes.
- Sleep stages/duration/efficiency and workout time/count/distance/calories are
  derived from `sleep` / `exercise` session points.

## Scheduled sync

`ops/com.fitbit-plus.sync.plist` runs `cli.py sync` every 6 hours via launchd:

```bash
cp ops/com.fitbit-plus.sync.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.fitbit-plus.sync.plist
```

Logs land in `~/Library/Logs/fitbit-plus-sync.log`; exit code 2 means the 7-day
Testing-mode token died — re-run `python cli.py auth`.
