"""The light curve's shape, pinned. These sweep pure functions — no hardware,
no DB. Times are naive local (the engine's convention); solar math runs on
this machine's zone (America/Denver), like production."""
from datetime import date, datetime, timezone

from app import circadian
from app.circadian import FULL, MIREDS_COOL, MIREDS_DAY, MIREDS_FLOOR, anchors, target

ITEMS = [
    {"time": "07:15", "label": "Wake", "color": "neutral"},
    {"time": "07:45", "label": "Deep rep — 90 min", "color": "focus"},
    {"time": "18:15", "label": "Gym or run", "color": "move"},
    {"time": "22:00", "label": "Wind down + pick tomorrow's task", "color": "wind"},
    {"time": "23:15", "label": "In bed — hard stop", "color": "neutral"},
]

D = date(2026, 6, 22)  # near-solstice: unambiguous high sun at midday


def at(hh: int, mm: int) -> datetime:
    return datetime(D.year, D.month, D.day, hh, mm)


def test_anchors_find_the_users_blocks():
    a = anchors(ITEMS)
    assert a["wake"] == 7 * 60 + 15
    assert a["wind"] == 22 * 60
    assert a["bed"] == 23 * 60 + 15


def test_deep_night_is_off():
    for hh, mm in ((3, 0), (23, 40), (0, 30), (6, 40)):
        t = target(at(hh, mm), ITEMS)
        assert t.on is False, f"{hh}:{mm} should be lights-off"
        assert t.reason in ("night",)


def test_sunrise_ramp_rises_to_wake():
    start = target(at(6, 51), ITEMS)          # ramp begins 06:50
    late = target(at(7, 14), ITEMS)
    assert start.on and late.on
    assert start.reason == late.reason == "sunrise"
    assert start.level < 30                   # ember start
    assert late.level > 200                   # near full at wake
    assert start.mireds > late.mireds         # warms → cools
    # monotonic through the ramp
    levels = [target(at(7, m), ITEMS).level for m in range(0, 15, 2)]
    assert levels == sorted(levels)


def test_morning_boost_is_the_light_dose():
    t = target(at(7, 30), ITEMS)
    assert t.on and t.level == FULL and t.mireds == MIREDS_DAY
    assert t.reason == "morning-boost"


def test_midday_tracks_high_sun():
    t = target(at(12, 30), ITEMS)
    assert t.on and t.reason == "day-high-sun"
    assert t.mireds == MIREDS_COOL


def test_away_daytime_is_off():
    t = target(at(12, 30), ITEMS, home=False)
    assert t.on is False and t.reason == "away"


def test_wind_down_fades_warm():
    mid = target(at(22, 15), ITEMS)
    assert mid.on and mid.reason == "wind-down"
    assert 25 <= mid.level <= 120
    late = target(at(22, 45), ITEMS)          # past the fade: amber hold
    assert late.level == 25 and late.mireds == MIREDS_FLOOR


def test_lights_out_fade_then_night():
    fading = target(at(23, 18), ITEMS)
    assert fading.reason == "lights-out" and fading.level <= 20
    gone = target(at(23, 30), ITEMS)
    assert gone.on is False


def test_missing_blocks_degrade_to_defaults():
    t = target(at(3, 0), [])
    assert t.on is False                      # defaults still give a sane night


def test_no_wake_block_means_no_sunrise_alarm():
    """His weekends: no Wake block → the bedroom stays dark until 10:00,
    then joins the solar day. No 6:50 ramp on a sleep-in day."""
    weekend = [i for i in ITEMS if "wake" not in i["label"].lower()]
    assert target(at(7, 0), weekend).on is False
    assert target(at(8, 30), weekend).on is False
    assert target(at(9, 59), weekend).on is False
    late_morning = target(at(11, 30), weekend)
    assert late_morning.on is True
    assert "day" in late_morning.reason


def test_curve_points_cover_the_day():
    pts = circadian.curve_points(D, ITEMS, step_min=10)
    assert len(pts) == 144
    assert pts[0]["time"] == "00:00" and pts[-1]["time"] == "23:50"
    assert any(p["reason"] == "sunrise" for p in pts)
    assert any(p["reason"] == "wind-down" for p in pts)


def test_solar_elevation_sanity():
    noon = circadian.solar_elevation(datetime(2026, 6, 22, 19, 0, tzinfo=timezone.utc))
    night = circadian.solar_elevation(datetime(2026, 6, 22, 8, 0, tzinfo=timezone.utc))
    assert noon > 60                          # SLC near-solstice midday sun
    assert night < 0                          # 2am local
