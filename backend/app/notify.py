"""Post-sync macOS notifications — the app reaching out when something needs you.

Runs at the end of every `cli.py sync` (best-effort, never fails the sync). Three
conditions are worth interrupting for:

  token    — the 7-day Testing-mode refresh token has ≤2 days left (or is dead).
             Without this, the token dies silently unless you happen to open the app.
  vitals   — the insights engine's multi-vital early-warning fired (≥2 vitals off
             together — the "getting sick / overreaching" guardrail).
  goal     — a goal streak that had reached ≥3 days just broke.

Notifications dedupe through a small state file (backend/notify_state.json, gitignored):
each alert key records when it last fired and re-arms only after its cooldown, so a
6-hourly launchd sync doesn't nag four times a day. Delivery is `osascript`'s
`display notification` — built into macOS, no extra dependencies; on other platforms
this module quietly does nothing.
"""
from __future__ import annotations

import json
import platform
import subprocess
from datetime import datetime, timezone
from typing import Any

from . import auth, goals, insights
from .config import BASE_DIR

STATE_FILE = BASE_DIR / "notify_state.json"

# Re-arm windows, in hours. Token nags daily while the clock runs down; the others
# stay quiet until the condition clears and re-fires.
COOLDOWN_H = {"token": 24.0, "vitals": 24.0, "goal": 48.0}

# A streak this long is an achievement worth defending; breaking it is the signal.
STREAK_WORTH_DEFENDING = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=1))


def _cooled_down(state: dict, key: str, kind: str) -> bool:
    last = (state.get("fired") or {}).get(key)
    if not last:
        return True
    try:
        age_h = (_now() - datetime.fromisoformat(last)).total_seconds() / 3600
    except ValueError:
        return True
    return age_h >= COOLDOWN_H.get(kind, 24.0)


# --- the three checks ----------------------------------------------------------

def _token_alert() -> tuple[str, str, str] | None:
    days = auth.token_days_left()
    if not auth.has_valid_token():
        return ("token", "Sign-in expired",
                "The Google Health token is dead — run `python cli.py auth` to reconnect.")
    if days is not None and days <= 2:
        return ("token", "Token expires soon",
                f"~{max(days, 0):g} days left on the 7-day token. "
                "Re-run `python cli.py auth` when convenient.")
    return None


def _vitals_alert() -> tuple[str, str, str] | None:
    fired = next((i for i in insights.compute(limit=24) if i.get("id") == "vitals-watch"), None)
    if not fired:
        return None
    return ("vitals", fired.get("title") or "Vitals drifting together",
            (fired.get("detail") or "")[:180])


def _goal_alerts(state: dict) -> list[tuple[str, str, str]]:
    """Compare current streaks to the last-seen snapshot; alert on broken ones."""
    evaluated = goals.evaluate_all().get("goals", [])
    previous: dict[str, int] = state.get("streaks") or {}
    alerts = []
    for g in evaluated:
        if g.get("status") == "no-data":
            continue
        prior = previous.get(g["data_type"], 0)
        if prior >= STREAK_WORTH_DEFENDING and g.get("streak", 0) == 0:
            alerts.append((f"goal:{g['data_type']}",
                           f"{g['label']} streak broken",
                           f"The {prior}-day run ended — yesterday missed "
                           f"{'≥' if g['comparator'] == 'gte' else '≤'}{g['target']:g}"
                           f"{' ' + g['unit'] if g['unit'] else ''}. One good day restarts it."))
    # Snapshot for next run regardless of whether anything fired.
    state["streaks"] = {g["data_type"]: g.get("streak", 0) for g in evaluated}
    return alerts


# --- delivery --------------------------------------------------------------------

def _send_macos(title: str, body: str) -> bool:
    if platform.system() != "Darwin":
        return False
    # osascript inside string literals: escape backslashes/quotes defensively.
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
    script = (f'display notification "{esc(body)}" '
              f'with title "fettle" subtitle "{esc(title)}" sound name "Glass"')
    try:
        proc = subprocess.run(["osascript", "-e", script],
                              capture_output=True, text=True, timeout=10)
        return proc.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def check_and_send() -> list[dict]:
    """Evaluate all conditions, send what's due, persist state. Returns what fired
    (also printed by cli.py so the launchd log shows the story)."""
    state = _load_state()
    candidates: list[tuple[str, str, str]] = []

    token = _token_alert()
    if token:
        candidates.append(token)
    try:
        vitals = _vitals_alert()
        if vitals:
            candidates.append(vitals)
    except Exception:  # noqa: BLE001 — detectors need data; never break notify on them
        pass
    try:
        candidates.extend(_goal_alerts(state))
    except Exception:  # noqa: BLE001
        pass

    sent = []
    for key, title, body in candidates:
        kind = key.split(":", 1)[0]
        if not _cooled_down(state, key, kind):
            continue
        delivered = _send_macos(title, body)
        state.setdefault("fired", {})[key] = _now().isoformat()
        sent.append({"key": key, "title": title, "body": body, "delivered": delivered})

    _save_state(state)
    return sent
