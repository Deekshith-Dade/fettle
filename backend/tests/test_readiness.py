"""The readiness engine's day-selection and component math.

Pins the mechanic behind "the hero showed 81 in the morning, 75 after sync":
today_breakdown scores the latest day with ≥2 components, so before overnight
data lands the latest scorable day IS yesterday. Also pins prior-day load,
weight renormalization, and the exact flat-baseline score (a deliberate tripwire:
changing WEIGHTS or a sub-score formula should fail a test).
"""
import types
from datetime import date, timedelta

from app import readiness

D = date(2026, 6, 30)  # fixed anchor — the engine never calls today()


def _rows(values_by_days_back: dict[int, float]) -> list[dict]:
    return [
        {"day": (D - timedelta(days=b)).isoformat(), "value": v, "unit": ""}
        for b, v in sorted(values_by_days_back.items(), key=lambda p: -p[0])
    ]


def _install(monkeypatch, tables: dict[str, list[dict]]) -> None:
    monkeypatch.setattr(
        readiness, "store",
        types.SimpleNamespace(query_daily=lambda dt, start, end: tables.get(dt, [])),
    )


def _flat(value: float, days: int = 40, start: int = 0) -> dict[int, float]:
    return {b: value for b in range(start, start + days)}


def test_flat_baseline_component_scores_and_total(monkeypatch):
    _install(monkeypatch, {
        "daily-heart-rate-variability": _rows(_flat(50.0)),
        "daily-resting-heart-rate": _rows(_flat(60.0)),
        "sleep-duration": _rows(_flat(8.0)),
        "sleep-efficiency": _rows(_flat(95.0)),
        "cardio-load": _rows(_flat(30.0)),
        "daily-sleep-temperature-derivations": _rows(_flat(0.0)),
    })
    b = readiness.today_breakdown()
    assert b["date"] == D.isoformat()
    scores = {c["key"]: c["score"] for c in b["components"]}
    # At baseline: HRV/RHR anchor at 75, sleep = .6·100 + .4·95, load at par = 88, temp 0dev = 92.
    assert scores == {"hrv": 75, "rhr": 75, "sleep": 98, "load": 88, "temp": 92}
    assert b["score"] == 85  # Σ(score·weight) with weights .30/.20/.30/.12/.08
    assert b["tone"] == "primed"


def test_scores_yesterday_until_overnight_data_lands(monkeypatch):
    # Before the morning sync, no series has a row for D: the hero must show D-1.
    tables = {
        "daily-heart-rate-variability": _rows(_flat(50.0, days=30, start=1)),
        "daily-resting-heart-rate": _rows(_flat(60.0, days=30, start=1)),
        "sleep-duration": _rows(_flat(8.0, days=30, start=1)),
    }
    _install(monkeypatch, tables)
    assert readiness.today_breakdown()["date"] == (D - timedelta(days=1)).isoformat()

    # The sync lands last night's sleep + this morning's HRV: D becomes scorable.
    tables["sleep-duration"] = _rows({**_flat(8.0, days=30, start=1), 0: 7.4})
    tables["daily-heart-rate-variability"] = _rows({**_flat(50.0, days=30, start=1), 0: 52.0})
    assert readiness.today_breakdown()["date"] == D.isoformat()


def test_walks_past_days_with_fewer_than_two_components(monkeypatch):
    # Recent days have only sleep (one component); HRV+RHR exist only 35+ days back.
    _install(monkeypatch, {
        "sleep-duration": _rows(_flat(8.0, days=10, start=0)),
        "daily-heart-rate-variability": _rows(_flat(50.0, days=11, start=35)),
        "daily-resting-heart-rate": _rows(_flat(60.0, days=11, start=35)),
    })
    b = readiness.today_breakdown()
    assert b["date"] == (D - timedelta(days=35)).isoformat()
    assert {c["key"] for c in b["components"]} == {"hrv", "rhr"}


def test_load_component_reads_the_prior_day(monkeypatch):
    load = _flat(30.0)
    load[1] = 90.0  # yesterday's spike is what today pays for
    _install(monkeypatch, {
        "daily-heart-rate-variability": _rows(_flat(50.0)),
        "daily-resting-heart-rate": _rows(_flat(60.0)),
        "sleep-duration": _rows(_flat(8.0)),
        "sleep-efficiency": _rows(_flat(95.0)),
        "cardio-load": _rows(load),
        "daily-sleep-temperature-derivations": _rows(_flat(0.0)),
    })
    load_comp = next(c for c in readiness.today_breakdown()["components"] if c["key"] == "load")
    assert load_comp["value"] == 90
    assert load_comp["good"] is False
    assert load_comp["score"] == 30  # 88 − (90/32.14 − 1)·45 floors at the 30 clamp


def test_no_score_from_a_single_component(monkeypatch):
    _install(monkeypatch, {"sleep-duration": _rows(_flat(8.0))})
    assert readiness.today_breakdown() is None


def test_zero_sleep_row_is_an_artifact_not_a_short_night(monkeypatch):
    # A sync gap once wrote sleep-duration 0.0 for the latest day; the component must
    # drop out (and the day renormalize) rather than score a "0h night" as 0.
    _install(monkeypatch, {
        "daily-heart-rate-variability": _rows(_flat(50.0)),
        "daily-resting-heart-rate": _rows(_flat(60.0)),
        "sleep-duration": _rows({**_flat(8.0, start=1), 0: 0.0}),
    })
    b = readiness.today_breakdown()
    assert b["date"] == D.isoformat()
    assert "sleep" not in {c["key"] for c in b["components"]}


def test_weights_renormalize_over_available_components(monkeypatch):
    # Only HRV (75) and sleep (100, no efficiency series): (75·.3 + 100·.3)/.6 = 87.5 → 88.
    _install(monkeypatch, {
        "daily-heart-rate-variability": _rows(_flat(50.0)),
        "sleep-duration": _rows(_flat(8.0)),
    })
    b = readiness.today_breakdown()
    assert {c["key"] for c in b["components"]} == {"hrv", "sleep"}
    assert b["score"] == 88
