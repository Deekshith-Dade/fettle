"""The sleep deep-dive's debt and Tonight prescription, end to end on synthetic data.

Covers the paths live data hasn't exercised yet (the hard-training bump, the 9.5h
ceiling) and re-pins two fixed bugs: artifact nights minting phantom debt, and the
load bump falling back to active-zone-minutes when cardio-load is absent.
"""
import types
from datetime import date, timedelta

import pytest

from app import sleep_analysis
from app.sleep_analysis import TONIGHT_CEIL, TONIGHT_DEBT_CAP


def _rows(values_by_days_back: dict[int, float]) -> list[dict]:
    today = date.today()
    return [
        {"day": (today - timedelta(days=b)).isoformat(), "value": v, "unit": ""}
        for b, v in sorted(values_by_days_back.items(), key=lambda p: -p[0])
    ]


@pytest.fixture
def use_cache(monkeypatch):
    def _install(cache: dict) -> None:
        monkeypatch.setattr(
            sleep_analysis, "store",
            types.SimpleNamespace(query_daily_bulk=lambda: cache),
        )
    return _install


def _nights(values_newest_last: list[float]) -> dict[int, float]:
    """Nightly durations ending yesterday (days_back 1)."""
    n = len(values_newest_last)
    return {n - i: v for i, v in enumerate(values_newest_last)}


def test_debt_drives_a_capped_payback(use_cache):
    # 16 nights at need (7.5) then four 6h nights: 6h net debt, payback capped at 1h.
    use_cache({"sleep-duration": _rows(_nights([7.5] * 16 + [6.0] * 4))})
    d = sleep_analysis.detail()
    assert d["need_hours"] == 7.5
    assert d["debt"]["hours"] == 6.0
    assert d["debt"]["tone"] == "watch"
    assert d["tonight"]["debt_payback"] == TONIGHT_DEBT_CAP
    assert d["tonight"]["hours"] == 8.5
    assert d["tonight"]["capped"] is False
    assert "8h 30m" in d["tonight"]["message"]


def test_artifact_night_mints_no_phantom_debt(use_cache):
    # A 2h tracker fragment two days ago: charts keep it, the debt math must not.
    use_cache({"sleep-duration": _rows({**_nights([7.5] * 18), 2: 2.0})})
    d = sleep_analysis.detail()
    assert d["debt"]["hours"] == 0.0
    assert d["debt"]["tone"] == "good"
    assert d["tonight"]["hours"] == d["need_hours"] == 7.5
    assert any(n["duration"] == 2.0 for n in d["nights"])  # still visible in the chart


def test_hard_training_day_adds_a_bump(use_cache):
    load = {b: 50.0 for b in range(1, 29)}
    load[0] = 100.0  # today ≥ 1.35× the prior-28-day mean
    use_cache({
        "sleep-duration": _rows(_nights([7.5] * 20)),
        "cardio-load": _rows(load),
    })
    t = sleep_analysis.detail()["tonight"]
    assert t["load_bump"] == 0.25
    assert t["debt_payback"] == 0.0
    assert t["hours"] == 7.75
    assert "hard training" in t["message"]


def test_load_bump_falls_back_to_active_zone_minutes(use_cache):
    # No cardio-load series at all — the bump must read active-zone-minutes instead.
    load = {b: 40.0 for b in range(1, 29)}
    load[0] = 90.0
    use_cache({
        "sleep-duration": _rows(_nights([7.5] * 20)),
        "active-zone-minutes": _rows(load),
    })
    assert sleep_analysis.detail()["tonight"]["load_bump"] == 0.25


def test_quiet_training_day_gets_no_bump(use_cache):
    load = {b: 50.0 for b in range(0, 29)}  # today present but at baseline
    use_cache({
        "sleep-duration": _rows(_nights([7.5] * 20)),
        "cardio-load": _rows(load),
    })
    assert sleep_analysis.detail()["tonight"]["load_bump"] == 0.0


def test_tonight_never_exceeds_the_ceiling(use_cache):
    # Need clamps to 9.0 and heavy debt wants +1h: 10h raw must cap at 9.5.
    use_cache({"sleep-duration": _rows(_nights([9.6] * 16 + [5.0] * 4))})
    t = sleep_analysis.detail()["tonight"]
    assert t["need"] == 9.0
    assert t["debt_payback"] == 1.0
    assert t["hours"] == TONIGHT_CEIL
    assert t["capped"] is True
