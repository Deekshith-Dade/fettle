"""Sleep sync derives honest rows from staged, classic, and stub sessions.

Pins the 2026-07-15 incident: a Fitbit device-sync gap first produced a stage-less
session envelope (the old code wrote all-zero rows → readiness scored sleep 0), and
the night later came back CLASSIC (metadata.stagesStatus REJECTED_COVERAGE) — one
ASLEEP span the old code didn't count, so the zeros persisted through re-syncs.
Missing data must produce absent rows, never zeros.
"""
import types
from datetime import date

from app import sync

DAY = "2026-07-15"
TODAY = date(2026, 7, 15)
OFF = "-21600s"


def _t(hhmm: str) -> str:
    return f"2026-07-15T{hhmm}:00Z"


def _session(start: str, end: str, stages: list[tuple[str, str, str]]) -> dict:
    return {"sleep": {
        "interval": {"startTime": _t(start), "startUtcOffset": OFF,
                     "endTime": _t(end), "endUtcOffset": OFF},
        "stages": [{"startTime": _t(a), "endTime": _t(b), "type": ty} for a, b, ty in stages],
    }}


def _run(monkeypatch, sessions: list[dict]) -> dict[str, dict[str, float]]:
    """_sync_sleep against a canned session list; returns {metric: {day: value}}."""
    written: dict[str, dict[str, float]] = {}

    def upsert(metric, unit, values):
        written[metric] = dict(values)
        return len(values)

    monkeypatch.setattr(sync, "store", types.SimpleNamespace(
        upsert_daily_values=upsert, set_watermark=lambda *a: None))
    client = types.SimpleNamespace(list_all=lambda name: iter(sessions))
    res = sync._sync_sleep(client, TODAY)
    assert res.error is None
    return written


def test_staged_night_writes_every_metric(monkeypatch):
    w = _run(monkeypatch, [_session("05:30", "13:30", [
        ("05:30", "09:30", "LIGHT"), ("09:30", "11:00", "DEEP"),
        ("11:00", "12:30", "REM"), ("12:30", "13:30", "AWAKE"),
    ])])
    assert w["sleep-duration"][DAY] == 7.0
    assert w["sleep-light"][DAY] == 4.0
    assert w["sleep-deep"][DAY] == 1.5
    assert w["sleep-rem"][DAY] == 1.5
    assert w["sleep-awake"][DAY] == 1.0
    assert w["sleep-efficiency"][DAY] == 87.5
    assert w["sleep-score"][DAY] == 86  # 37.5 duration + 25 quality + 23.0 restoration


def test_classic_night_scores_without_stage_rows(monkeypatch):
    # A REJECTED_COVERAGE night: the whole sleep arrives as one ASLEEP span.
    w = _run(monkeypatch, [_session("05:30", "13:30", [("05:30", "13:30", "ASLEEP")])])
    assert w["sleep-duration"][DAY] == 8.0
    assert w["sleep-efficiency"][DAY] == 100.0
    # Unknown stages are absent, not zero — zeros would poison the 14-day stage averages.
    assert DAY not in w["sleep-deep"]
    assert DAY not in w["sleep-rem"]
    assert DAY not in w["sleep-light"]
    # And the score renormalizes over duration+restoration instead of zeroing quality.
    assert w["sleep-score"][DAY] == 100


def test_restless_counts_as_awake_on_classic_nights(monkeypatch):
    w = _run(monkeypatch, [_session("05:30", "13:30", [
        ("05:30", "12:30", "ASLEEP"), ("12:30", "13:00", "RESTLESS"),
        ("13:00", "13:30", "AWAKE"),
    ])])
    assert w["sleep-duration"][DAY] == 7.0
    assert w["sleep-awake"][DAY] == 1.0
    assert w["sleep-efficiency"][DAY] == 87.5


def test_unprocessed_stub_session_writes_nothing(monkeypatch):
    # The morning-of-incident shape: a session envelope with no sleep content yet.
    w = _run(monkeypatch, [_session("05:40", "13:32", [])])
    for metric, values in w.items():
        assert DAY not in values, f"{metric} wrote a row for a night with no data"
