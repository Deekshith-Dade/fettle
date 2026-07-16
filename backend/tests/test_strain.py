"""Strain — personal-scale normalization, the recovery-derived optimal band, and gating.

Pins: 100% = a p90 day (floored), a rest day ~0, the band tracks recovery, and no
cardio-load history yields None rather than a fake number.
"""
import types
from datetime import date

from app import strain

TODAY = date(2026, 7, 15)


def _rows(values: list[float]):
    return [{"day": f"2026-06-{i + 1:02d}", "value": v, "unit": ""} for i, v in enumerate(values)]


def _install(monkeypatch, loads: list[float], recovery: int | None):
    monkeypatch.setattr(strain, "store",
                        types.SimpleNamespace(query_daily=lambda dt, s, e: _rows(loads)))
    monkeypatch.setattr(strain, "readiness",
                        types.SimpleNamespace(today_breakdown=lambda: ({"score": recovery} if recovery is not None else None)))


def test_hard_day_scales_toward_100(monkeypatch):
    # p90 of this set is ~90; a final day AT the top of the range reads near 100%.
    _install(monkeypatch, [10, 20, 30, 40, 50, 60, 70, 80, 90, 95], recovery=70)
    r = strain.today(TODAY)
    assert r is not None
    assert r["score"] >= 90


def test_rest_day_reads_low(monkeypatch):
    _install(monkeypatch, [10, 20, 30, 40, 50, 60, 70, 80, 200, 3], recovery=70)
    assert strain.today(TODAY)["score"] <= 10


def test_floor_stops_sparse_data_pegging_100(monkeypatch):
    # Tiny loads: without the REF_FLOOR, today's 30 over a p90 of ~30 would read 100%.
    _install(monkeypatch, [5, 10, 15, 20, 30], recovery=70)
    r = strain.today(TODAY)
    assert r["score"] == round(100 * 30 / strain.REF_FLOOR)  # scaled by the floor, not p90


def test_optimal_band_tracks_recovery(monkeypatch):
    _install(monkeypatch, [50] * 10, recovery=80)
    hi_rec = strain.today(TODAY)["target"]
    assert hi_rec == {"lo": 44, "hi": 68}  # 80·0.55, 80·0.85
    _install(monkeypatch, [50] * 10, recovery=40)
    lo_rec = strain.today(TODAY)["target"]
    assert lo_rec["hi"] < hi_rec["hi"]  # run-down day → lower ceiling


def test_no_band_without_recovery(monkeypatch):
    _install(monkeypatch, [50] * 10, recovery=None)
    r = strain.today(TODAY)
    assert r["target"] is None
    assert r["tone"] == "neutral"


def test_no_load_history_returns_none(monkeypatch):
    _install(monkeypatch, [], recovery=70)
    assert strain.today(TODAY) is None
