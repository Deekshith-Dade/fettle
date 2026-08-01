"""Circadian lighting engine — fettle drives the bedside lamp through the day.

Every tick (30s) the engine reads the lamp's actual state, decides the current
setpoint, and applies it with a gentle Matter transition. Decision layering,
lowest to highest:

  circadian curve (circadian.py, anchored to the user's own schedule blocks)
    < presence (phone's Tailscale direct endpoint on the home LAN = home)
      < manual-touch holdoff (lamp changed outside fettle → back off 2 h;
        automation that fights the human gets unplugged)
        < explicit override (focus / movie / warm / off, with a duration)

State that must survive restarts (holdoff, override, last-applied) lives in
SQLite via store.py. Every transition — ours or the human's — lands in
light_log, which is what the dashboard strip and the adherence analytics read.

The Matter sidecar being down is a logged, non-fatal condition: the engine
just tries again next tick (KeepAlive restarts the sidecar independently).
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from datetime import date, datetime, timedelta
from typing import Any

from . import circadian, matter, schedule, store
from .circadian import LightTarget
from .config import settings

log = logging.getLogger("fettle.lights")

# Attribute paths on the lamp (endpoint/cluster/attribute).
_P_ONOFF = "{ep}/6/0"
_P_LEVEL = "{ep}/8/0"
_P_CT = "{ep}/768/7"

HOLDOFF = timedelta(hours=2)
_LEVEL_TOL = 10       # Level replies wobble a little during transitions
_MIREDS_TOL = 40

OVERRIDE_MODES: dict[str, dict] = {
    # name -> target fields (None level/mireds = leave as-is)
    "focus": {"on": True, "level": 254, "mireds": circadian.MIREDS_COOL},
    "movie": {"on": True, "level": 30, "mireds": circadian.MIREDS_FLOOR},
    "warm":  {"on": True, "level": 120, "mireds": circadian.MIREDS_WARM},
    "off":   {"on": False, "level": 0, "mireds": circadian.MIREDS_WARM},
}


# --- presence -----------------------------------------------------------------

_presence_cache: dict[str, Any] = {"at": None, "home": True}
_TS_BIN = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def _phone_home_sync() -> bool:
    """Home = the phone's Tailscale direct endpoint is a home-LAN address.
    On the tailnet from LTE the endpoint is a public IP; at home it's 192.168.x.
    Unknown (relayed, CLI hiccup, no phone) fails open to 'home' — a lamp that
    runs while you're out beats one that's dark when you're in."""
    try:
        out = subprocess.run([_TS_BIN, "status", "--json"], capture_output=True,
                             text=True, timeout=10).stdout
        peers = json.loads(out).get("Peer") or {}
        for p in peers.values():
            name = (p.get("HostName") or "").lower()
            if settings.lights_presence_device in name:
                cur = p.get("CurAddr") or ""
                if not p.get("Online"):
                    return False
                if cur.startswith(("192.168.", "10.")):
                    return True
                return False if cur else True  # relayed/unknown → assume home
    except Exception as exc:  # noqa: BLE001 — presence must never kill a tick
        log.debug("presence check failed: %s", exc)
    return True


async def _phone_home() -> bool:
    now = datetime.now()
    if _presence_cache["at"] and (now - _presence_cache["at"]).total_seconds() < 300:
        return _presence_cache["home"]
    home = await asyncio.to_thread(_phone_home_sync)
    _presence_cache.update(at=now, home=home)
    return home


# --- lamp I/O -----------------------------------------------------------------

async def read_lamp() -> dict | None:
    """Actual device state, or None when the sidecar/lamp is unreachable."""
    ep = settings.lamp_endpoint
    try:
        vals = await matter.read_attributes(
            [_P_ONOFF.format(ep=ep), _P_LEVEL.format(ep=ep), _P_CT.format(ep=ep)])
    except Exception as exc:  # noqa: BLE001
        log.warning("lamp read failed: %s", exc)
        return None
    def pick(path): return vals.get(path.format(ep=ep))
    if pick(_P_ONOFF) is None:
        return None
    return {"on": bool(pick(_P_ONOFF)),
            "level": pick(_P_LEVEL),
            "mireds": pick(_P_CT)}


async def apply(t: LightTarget, source: str, prev: dict | None = None) -> bool:
    """Drive the lamp to the target (prev = its state at command time, for the
    fade envelope). Returns True when commands were accepted."""
    tenths = max(0, t.transition_s * 10 // 1)
    try:
        if t.on:
            await matter.device_command(8, "MoveToLevelWithOnOff", {
                "level": max(1, min(254, t.level)),
                "transitionTime": tenths, "optionsMask": 0, "optionsOverride": 0})
            await matter.device_command(768, "MoveToColorTemperature", {
                "colorTemperatureMireds": max(circadian.MIREDS_COOL,
                                              min(circadian.MIREDS_FLOOR, t.mireds)),
                "transitionTime": tenths, "optionsMask": 0, "optionsOverride": 0})
        else:
            await matter.device_command(6, "Off", {})
    except Exception as exc:  # noqa: BLE001
        log.warning("lamp apply failed: %s", exc)
        return False
    store.light_log_add(source=source, on=t.on, level=t.level,
                        mireds=t.mireds, reason=t.reason)
    # The lamp eases far slower than the commanded transitionTime (a 455→154
    # mireds fade takes minutes), so reads land mid-fade long after the nominal
    # transition. Remember where the fade STARTED (prev_*) and give it a wide
    # settle window; the drift detector then treats anything between start and
    # target as "still fading" and only flags values outside that envelope.
    settle = (datetime.now() + timedelta(seconds=max(300, t.transition_s * 3))
              ).isoformat(timespec="seconds")
    extra = {"settle_until": settle}
    if prev is not None:
        extra |= {"prev_level": prev.get("level"), "prev_mireds": prev.get("mireds")}
    store.light_state_save(last_applied=t.as_dict() | extra)
    return True


def _outside(value: float | None, a: float | None, b: float | None, tol: int) -> bool:
    """Is `value` outside the [a↔b] fade envelope (± tol)? Unknowns → inside."""
    if value is None or a is None or b is None:
        return False
    lo, hi = min(a, b) - tol, max(a, b) + tol
    return not (lo <= value <= hi)


def _drifted(actual: dict, applied: dict | None, settling: bool = False) -> bool:
    """Did a human change the lamp behind our back? While our own command is
    still settling, anything between the fade's start and its target is just
    the lamp taking its time — only values outside that envelope are a hand."""
    if not applied:
        return False
    if actual["on"] != applied["on"]:
        return True
    if not actual["on"]:
        return False  # off is off, whatever the stored knobs say
    if settling:
        return (_outside(actual["level"], applied.get("prev_level"),
                         applied["level"], _LEVEL_TOL)
                or _outside(actual["mireds"], applied.get("prev_mireds"),
                            applied["mireds"], _MIREDS_TOL))
    if actual["level"] is not None and abs(actual["level"] - applied["level"]) > _LEVEL_TOL:
        return True
    if actual["mireds"] is not None and abs(actual["mireds"] - applied["mireds"]) > _MIREDS_TOL:
        return True
    return False


# --- light journeys (timed arcs: nap → fade / dark / gentle wake) -------------

def _mix(a: float, b: float, p: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, p))


def build_nap(minutes: int, now: datetime | None = None) -> dict:
    """A nap journey: fade to darkness, hold dark, and end on a gentle amber
    wake ramp — the light, not an alarm, ends the nap. 15..240 minutes."""
    now = now or datetime.now()
    minutes = max(15, min(240, int(minutes)))
    fade = 3 if minutes >= 25 else 0        # short naps: straight to dark
    wake = 8 if minutes >= 25 else 5
    return {"kind": "nap", "minutes": minutes, "fade": fade, "wake": wake,
            "started": now.isoformat(timespec="seconds"),
            "ends": (now + timedelta(minutes=minutes)).isoformat(timespec="seconds")}


def journey_target(j: dict, now: datetime) -> LightTarget | None:
    """The journey's setpoint at `now`, or None once it has run its course."""
    started = datetime.fromisoformat(j["started"])
    el = (now - started).total_seconds() / 60
    m, fade, wake = j["minutes"], j["fade"], j["wake"]
    if el < 0 or el >= m:
        return None
    if el < fade:                            # ease out of the current light
        p = el / fade
        return LightTarget(True, round(_mix(40, 1, p)), circadian.MIREDS_FLOOR,
                           "nap-fade", 60)
    if el < m - wake:                        # the nap itself
        return LightTarget(False, 0, circadian.MIREDS_FLOOR, "nap-dark", 30)
    p = (el - (m - wake)) / wake             # ember rising — the gentle alarm
    return LightTarget(True, round(_mix(2, 115, p * p)),
                       round(_mix(circadian.MIREDS_FLOOR, circadian.MIREDS_WARM, p)),
                       "nap-wake", 60)


def start_journey(kind: str, minutes: int) -> dict:
    if kind != "nap":
        raise ValueError("only 'nap' journeys exist so far")
    j = build_nap(minutes)
    # A journey supersedes whatever else was going on — including a holdoff:
    # asking for a nap IS the human taking control.
    store.light_state_save(journey=j, override_mode=None, override_until=None,
                           holdoff_until=None)
    return j


# --- the tick -----------------------------------------------------------------

async def tick(now: datetime | None = None) -> dict:
    """One engine pass; returns a status dict (also what /api/lights reports)."""
    now = now or datetime.now()
    st = store.light_state_get()
    items = schedule.day_view(now.date())["items"]
    home = await _phone_home()
    actual = await read_lamp()

    status: dict[str, Any] = {"ts": now.isoformat(timespec="seconds"),
                              "reachable": actual is not None,
                              "home": home, "actual": actual}

    if actual is None:
        return status

    # A human touched the lamp → respect it, hold off, remember what we saw.
    # During our own fade, only values outside the start↔target envelope count.
    applied = st.get("last_applied")
    now_iso = now.isoformat(timespec="seconds")
    settling = bool(applied and (applied.get("settle_until") or "") > now_iso)
    if _drifted(actual, applied, settling=settling):
        until = now + HOLDOFF
        # A hand on the lamp also ends any running journey — standing up and
        # turning the light on mid-nap means the nap is over.
        store.light_state_save(holdoff_until=until.isoformat(timespec="seconds"),
                               journey=None,
                               last_applied=actual | {"reason": "manual",
                                                      "transition_s": 0})
        store.light_log_add(source="manual", on=actual["on"],
                            level=actual.get("level") or 0,
                            mireds=actual.get("mireds") or 0, reason="hand-adjusted")
        log.info("manual change detected — engine holds off until %s", until)
        st = store.light_state_get()

    # Decide the setpoint: journey > override > holdoff > curve.
    journey = st.get("journey")
    if journey:
        jt = journey_target(journey, now)
        if jt is None:                       # ran its course — back to the day
            store.light_state_save(journey=None)
            store.light_log_add(source="engine", on=True, level=0, mireds=0,
                                reason=f"{journey.get('kind', 'journey')}-complete")
            journey = None
            st = store.light_state_get()
        else:
            status["journey"] = {"kind": journey.get("kind"),
                                 "ends": journey.get("ends"), "phase": jt.reason}
            status["target"] = jt.as_dict()
            reason_same = (applied or {}).get("reason") == jt.reason
            if not (reason_same and (settling or not _drifted(actual, jt.as_dict()))):
                if await apply(jt, source="engine", prev=actual):
                    status["applied"] = True
            return status

    # Decide the setpoint: override > holdoff > curve.
    override_until = st.get("override_until")
    if override_until and override_until > now.isoformat(timespec="seconds"):
        mode = st.get("override_mode") or "warm"
        f = OVERRIDE_MODES.get(mode, OVERRIDE_MODES["warm"])
        tgt = LightTarget(f["on"], f["level"], f["mireds"], f"override:{mode}", 10)
        status["override"] = {"mode": mode, "until": override_until}
    elif (h := st.get("holdoff_until")) and h > now.isoformat(timespec="seconds"):
        status["holdoff_until"] = h
        status["target"] = None
        return status
    else:
        tgt = circadian.target(now, items, home=home)

    status["target"] = tgt.as_dict()

    # Only talk to the lamp when the setpoint moved: same reason and either the
    # lamp already matches, or our previous command is still fading toward it.
    reason_same = (applied or {}).get("reason") == tgt.reason
    if reason_same and (settling or not _drifted(actual, tgt.as_dict())):
        return status
    if await apply(tgt, source="engine", prev=actual):
        status["applied"] = True
    return status


async def run_engine(stop: asyncio.Event) -> None:
    """The lifespan loop. Never raises; a broken tick logs and waits."""
    log.info("circadian light engine up (tick %ss)", settings.lights_tick_seconds)
    while not stop.is_set():
        if settings.lights_enabled:
            try:
                await tick()
            except Exception:  # noqa: BLE001
                log.exception("light tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.lights_tick_seconds)
        except asyncio.TimeoutError:
            pass
    log.info("circadian light engine stopped")


# --- API-facing helpers -------------------------------------------------------

def set_override(mode: str, minutes: int) -> dict:
    if mode not in OVERRIDE_MODES:
        raise ValueError(f"mode must be one of {sorted(OVERRIDE_MODES)}")
    until = (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    store.light_state_save(override_mode=mode, override_until=until)
    return {"mode": mode, "until": until}


def clear_override() -> None:
    """Hand everything back to the curve: override, holdoff, and any journey."""
    store.light_state_save(override_mode=None, override_until=None,
                           holdoff_until=None, journey=None)


async def status_now() -> dict:
    """Status + today's plan, for the dashboard card."""
    now = datetime.now()
    st = await tick(now)
    items = schedule.day_view(now.date())["items"]
    st["curve"] = circadian.curve_points(now.date(), items)
    st["anchors"] = circadian.anchors(items)
    st["log"] = store.light_log_recent(hours=24)
    return st
