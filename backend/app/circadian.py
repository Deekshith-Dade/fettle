"""The light curve — what the bedside lamp should be doing at any instant.

Pure functions of (clock time, that day's schedule items, presence), so tests
can sweep a whole day in milliseconds. The engine (lights.py) layers overrides
and manual-touch holdoffs on top; nothing here talks to hardware.

Shape of a day (all boundaries from the user's own schedule blocks):

    night ──▶ sunrise ramp ──▶ morning boost ──▶ solar day ──▶ evening warm
      ▲        (wake-25m→wake)   (wake→+45m,       (CCT tracks     (sunset…)
      │         0→full, ember→    full cool —      sun elevation)
      │         daylight)         the light dose)
      └── lights-out (bed block) ◀── wind-down fade (wind block → +30m)

The lamp's CCT floor is ~2200K (455 mireds); the deep-night ember tones stay
at that floor with low brightness rather than pretending to be candlelight
via RGB — Matter color moves are already smooth there.

Solar elevation: NOAA's simplified SPA (±0.2°, fine for lighting).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone

from .config import settings

# Matter Level cluster is 1..254; ColorTemperature is in mireds (1e6/K).
FULL = 254
MIREDS_COOL = 154        # 6500K — the lamp's coolest
MIREDS_DAY = 200         # 5000K
MIREDS_NEUTRAL = 250     # 4000K
MIREDS_WARM = 370        # 2700K
MIREDS_FLOOR = 455       # 2200K — the lamp's warmest CCT

SUNRISE_RAMP_MIN = 25    # ends exactly at the Wake block
MORNING_BOOST_MIN = 45   # full-cool light dose after wake
WINDDOWN_FADE_MIN = 30
BED_FADE_MIN = 10


@dataclass
class LightTarget:
    on: bool
    level: int          # 1..254 (meaningful only when on)
    mireds: int         # color temperature (meaningful only when on)
    reason: str         # which segment of the day decided this
    transition_s: int   # how gently to get there

    def as_dict(self) -> dict:
        return asdict(self)


def kelvin(mireds: int) -> int:
    return round(1_000_000 / mireds)


def solar_elevation(when_utc: datetime, lat: float | None = None,
                    lon: float | None = None) -> float:
    """Sun elevation in degrees (NOAA simplified). `when_utc` must be UTC."""
    lat = settings.home_lat if lat is None else lat
    lon = settings.home_lon if lon is None else lon
    doy = when_utc.timetuple().tm_yday
    frac_hour = when_utc.hour + when_utc.minute / 60 + when_utc.second / 3600
    g = 2 * math.pi / 365 * (doy - 1 + (frac_hour - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    tst = frac_hour * 60 + eqtime + 4 * lon          # true solar time, minutes
    ha = math.radians(tst / 4 - 180)                 # hour angle
    lat_r = math.radians(lat)
    cos_zen = (math.sin(lat_r) * math.sin(decl)
               + math.cos(lat_r) * math.cos(decl) * math.cos(ha))
    return 90 - math.degrees(math.acos(max(-1.0, min(1.0, cos_zen))))


# --- schedule anchors ---------------------------------------------------------

def _parse_hhmm(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def anchors(items: list[dict]) -> dict[str, int | None]:
    """Pull the day's light-relevant times (minutes since midnight) out of the
    schedule items. Keyed on the block taxonomy the user already lives by:
    a 'wake' label, the wind-down block (color 'wind' or label match), and the
    in-bed block. Missing blocks → None; the curve degrades gracefully."""
    wake = wind = bed = None
    for it in items:
        t = it.get("time")
        if not t:
            continue
        mins = _parse_hhmm(t)
        label = (it.get("label") or "").lower()
        if wake is None and "wake" in label:
            wake = mins
        if wind is None and (it.get("color") == "wind" or "wind down" in label):
            wind = mins
        if "bed" in label or "sleep" in label:
            bed = mins if bed is None else max(bed, mins)
    return {"wake": wake, "wind": wind, "bed": bed}


def _lerp(a: float, b: float, p: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, p))


# --- the curve ----------------------------------------------------------------

def target(now_local: datetime, items: list[dict], home: bool = True) -> LightTarget:
    """The engine's setpoint for this instant, before overrides."""
    a = anchors(items)
    mins = now_local.hour * 60 + now_local.minute + now_local.second / 60
    wake_known = a["wake"] is not None
    wake = a["wake"] if wake_known else 7 * 60 + 15
    wind = a["wind"] if a["wind"] is not None else 22 * 60
    bed = a["bed"] if a["bed"] is not None else 23 * 60 + 15
    # No wake block that day (his weekends) → no sunrise alarm for a sleeping
    # room; the lamp stays dark until a civilized hour, then joins the sun.
    ramp_start = wake - SUNRISE_RAMP_MIN if wake_known else 10 * 60

    # Deep night: after lights-out (plus fade) or before the sunrise ramp.
    if mins >= bed + BED_FADE_MIN or mins < ramp_start:
        return LightTarget(False, 0, MIREDS_FLOOR, "night", 30)

    # Lights-out fade at the in-bed block.
    if mins >= bed:
        p = (mins - bed) / BED_FADE_MIN
        lvl = round(_lerp(20, 1, p))
        return LightTarget(lvl > 1, lvl, MIREDS_FLOOR, "lights-out", 60)

    # Sunrise ramp: ember → daylight, timed to finish at the Wake block.
    if wake_known and mins < wake:
        p = (mins - ramp_start) / SUNRISE_RAMP_MIN
        return LightTarget(True, round(_lerp(3, FULL, p * p)),   # ease-in
                           round(_lerp(MIREDS_FLOOR, MIREDS_DAY, p)),
                           "sunrise", 60)

    # Morning boost: the circadian light dose.
    if wake_known and mins < wake + MORNING_BOOST_MIN:
        return LightTarget(True, FULL, MIREDS_DAY, "morning-boost", 60)

    # Wind-down fade and the amber hold after it.
    if mins >= wind:
        p = (mins - wind) / WINDDOWN_FADE_MIN
        return LightTarget(True, round(_lerp(120, 25, p)),
                           round(_lerp(MIREDS_WARM, MIREDS_FLOOR, p)),
                           "wind-down", 90)

    # Solar day / evening: CCT and brightness track the sun. Away → off.
    if not home:
        return LightTarget(False, 0, MIREDS_NEUTRAL, "away", 30)
    # Naive datetimes are local wall clock; astimezone() stamps the system zone.
    elev = solar_elevation(now_local.astimezone().astimezone(timezone.utc))
    if elev >= 25:
        return LightTarget(True, 230, MIREDS_COOL, "day-high-sun", 120)
    if elev >= 5:
        p = (elev - 5) / 20
        return LightTarget(True, round(_lerp(180, 230, p)),
                           round(_lerp(MIREDS_DAY, MIREDS_COOL, p)),
                           "day", 120)
    if elev >= -4:
        p = (elev + 4) / 9
        return LightTarget(True, round(_lerp(140, 180, p)),
                           round(_lerp(MIREDS_WARM, MIREDS_DAY, p)),
                           "golden-hour", 120)
    return LightTarget(True, 120, MIREDS_WARM, "evening", 120)


def curve_points(day: date, items: list[dict], step_min: int = 10) -> list[dict]:
    """The whole day's planned curve (presence assumed home) for the dashboard
    strip — one point per `step_min`."""
    out = []
    for m in range(0, 24 * 60, step_min):
        t = datetime(day.year, day.month, day.day, m // 60, m % 60)
        tgt = target(t, items, home=True)
        out.append({"time": f"{m // 60:02d}:{m % 60:02d}", **tgt.as_dict()})
    return out
