"""Vital Age: age-norm inversion, the no-double-count of RHR, bounds, and gating.

Pins the engine's contract — a person exactly at their age-norms reads their own age;
better HRV/fitness reads younger; the fitness component folds in resting HR (so RHR is
never a separate line); behaviour only nudges within bounds; and a thin history refuses
to produce a number rather than guessing.
"""
import types
from datetime import date

from app import vital_age

# A fixed "today" 30.00 years after the birth date the tests pin, so chrono age == 30.0.
TODAY = date(2030, 1, 1)
BORN = "1999-12-31"  # ~30.0y before TODAY


def _cfg(monkeypatch):
    monkeypatch.setattr(vital_age.settings, "birth_date", BORN, raising=False)
    monkeypatch.setattr(vital_age.settings, "sex", "male", raising=False)


def _rows(value, n=28):
    return [{"day": f"2029-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", "value": value, "unit": ""}
            for i in range(n)]


def _install(monkeypatch, series: dict):
    _cfg(monkeypatch)
    monkeypatch.setattr(vital_age, "store",
                        types.SimpleNamespace(query_daily_bulk=lambda: series))


def test_person_at_age_norms_reads_their_own_age(monkeypatch):
    # RMSSD median at 30 ≈ 100.4·e^(−0.723) ≈ 48.7; VO₂max median at 30 ≈ 44.45, which
    # the HR-ratio method reproduces at RHR ≈ 15.3·(211−19.2)/44.45 ≈ 66 bpm.
    _install(monkeypatch, {
        "daily-heart-rate-variability": _rows(48.7),
        "daily-resting-heart-rate": _rows(66.0),
    })
    r = vital_age.compute(TODAY)
    assert r["chronological_age"] == 30.0
    assert abs(r["vital_age"] - 30.0) < 1.0        # lands on chronological
    assert r["verdict"] == "on par"


def test_strong_hrv_and_fitness_read_younger(monkeypatch):
    _install(monkeypatch, {
        "daily-heart-rate-variability": _rows(75.0),   # ~a 20-yr-old's RMSSD
        "daily-resting-heart-rate": _rows(48.0),        # athlete RHR → high est. VO₂max
    })
    r = vital_age.compute(TODAY)
    assert r["vital_age"] < 30.0
    assert r["verdict"] == "younger"


def test_resting_hr_is_folded_into_fitness_not_double_counted(monkeypatch):
    _install(monkeypatch, {
        "daily-heart-rate-variability": _rows(48.7),
        "daily-resting-heart-rate": _rows(66.0),
    })
    keys = {c["key"] for c in vital_age.compute(TODAY)["components"]}
    assert "fitness" in keys
    assert "rhr" not in keys  # RHR only ever appears via the VO₂max estimate


def test_deviation_is_capped(monkeypatch):
    # Absurdly low HRV + very high RHR would blow past ±12y without the clamp.
    _install(monkeypatch, {
        "daily-heart-rate-variability": _rows(8.0),
        "daily-resting-heart-rate": _rows(110.0),
    })
    r = vital_age.compute(TODAY)
    assert r["delta_years"] <= vital_age.DEV_CAP
    assert r["vital_age"] <= 30.0 + vital_age.DEV_CAP


def test_thin_history_refuses_to_guess(monkeypatch):
    _install(monkeypatch, {  # only 6 nights — below MIN_NIGHTS
        "daily-heart-rate-variability": _rows(50.0, n=6),
        "daily-resting-heart-rate": _rows(60.0, n=6),
    })
    assert vital_age.compute(TODAY) is None


def test_no_cardiovascular_read_returns_none(monkeypatch):
    _install(monkeypatch, {  # steps only, no HRV or RHR
        "steps": _rows(9000.0),
    })
    assert vital_age.compute(TODAY) is None


def test_good_activity_pulls_age_down(monkeypatch):
    base = {
        "daily-heart-rate-variability": _rows(48.7),
        "daily-resting-heart-rate": _rows(66.0),
    }
    _install(monkeypatch, base)
    neutral = vital_age.compute(TODAY)["vital_age"]
    _install(monkeypatch, {**base,
                           "steps": _rows(12000.0),
                           "active-zone-minutes": _rows(60.0)})  # 420 min/wk
    assert vital_age.compute(TODAY)["vital_age"] < neutral
