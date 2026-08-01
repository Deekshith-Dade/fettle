"""The nap journey's arc, pinned: fade → dark → gentle wake, for the segment
lengths the user actually asks for (30m / 1h / 2h) plus the short-nap edge."""
from datetime import datetime, timedelta

from app.lights import build_nap, journey_target

T0 = datetime(2026, 8, 1, 14, 0)


def at(j, minutes: float):
    return journey_target(j, T0 + timedelta(minutes=minutes))


def test_hour_nap_has_all_three_phases():
    j = build_nap(60, T0)
    assert at(j, 1).reason == "nap-fade"
    assert at(j, 1).on is True
    assert at(j, 10).reason == "nap-dark"
    assert at(j, 10).on is False
    assert at(j, 45).reason == "nap-dark"
    wake = at(j, 55)
    assert wake.reason == "nap-wake" and wake.on is True
    assert wake.mireds >= 370                # ember → warm, never cool
    assert at(j, 60) is None                 # over — engine resumes the curve


def test_wake_ramp_rises_gently():
    j = build_nap(60, T0)
    lvls = [at(j, m).level for m in (52.5, 55, 57, 59.5)]
    assert lvls == sorted(lvls)
    assert lvls[0] <= 10 and lvls[-1] <= 130  # gentle, not a floodlight


def test_30_and_120_minute_segments():
    for m in (30, 120):
        j = build_nap(m, T0)
        assert at(j, m - j["wake"] - 1).reason == "nap-dark"
        assert at(j, m - 1).reason == "nap-wake"
        assert at(j, m) is None


def test_short_nap_skips_the_fade():
    j = build_nap(15, T0)
    assert j["fade"] == 0
    assert at(j, 0.5).reason == "nap-dark"   # straight to dark
    assert at(j, 12).reason == "nap-wake"


def test_duration_clamps():
    assert build_nap(3, T0)["minutes"] == 15
    assert build_nap(999, T0)["minutes"] == 240
