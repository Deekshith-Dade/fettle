"""Detector behavior the briefing quotes verbatim — σ wording, records, streaks —
plus the full compute() pipeline proving a partial today never reaches a detector."""
import statistics
import types
from datetime import date, timedelta

from app import insights


def _rows(values_by_days_back: dict[int, float], anchor: date | None = None) -> list[dict]:
    anchor = anchor or date.today()
    return [
        {"day": (anchor - timedelta(days=b)).isoformat(), "value": v, "unit": ""}
        for b, v in sorted(values_by_days_back.items(), key=lambda p: -p[0])
    ]


def test_anomaly_sigma_wording_and_sentiment():
    # 28 quiet days around 60 bpm, then a 65: 'N.Nσ above your 60 bpm average'.
    vals = {b: (59.5 if b % 2 else 60.5) for b in range(1, 29)}
    vals[0] = 65.0
    out = insights._anomalies({"daily-resting-heart-rate": _rows(vals, date(2026, 6, 30))})
    assert len(out) == 1
    a = out[0]
    hist = [v for b, v in vals.items() if b > 0]
    z = (65.0 - statistics.mean(hist)) / statistics.stdev(hist)
    assert f"{abs(z):.1f}σ above your 60 bpm average" in a["detail"]
    assert a["sentiment"] == "bad"  # RHR is lower-is-better; a spike is bad
    assert "spiked" in a["title"]


def test_record_fires_only_on_the_good_extreme():
    anchor = date(2026, 6, 30)
    rising = {b: 8000.0 + (14 - b) * 100 for b in range(0, 15)}
    out = insights._records({"steps": _rows(rising, anchor)})
    assert [i["metric"] for i in out] == ["steps"]
    assert out[0]["sentiment"] == "good"

    falling = {b: 8000.0 - (14 - b) * 100 for b in range(0, 15)}
    assert insights._records({"steps": _rows(falling, anchor)}) == []


def test_streak_counts_only_the_unbroken_tail():
    anchor = date(2026, 6, 30)
    vals = {b: 6.0 for b in range(6, 10)}          # older short nights break the run
    vals.update({b: 7.5 for b in range(0, 6)})     # six 7h+ nights, newest last
    out = insights._streaks({"sleep-duration": _rows(vals, anchor)})
    assert len(out) == 1
    assert out[0]["title"] == "6-day streak"
    assert "6 days running of 7h+ sleep" in out[0]["detail"]


def test_compute_quarantines_todays_partial_steps(monkeypatch):
    # The 2026-07-11 bug, end to end: 30 healthy days then 275 steps at 9 AM today.
    # complete_days must drop the partial before any detector sees it.
    vals = {b: (8000.0 if b % 2 else 9000.0) for b in range(1, 31)}
    vals[0] = 275.0
    bulk = {"steps": _rows(vals)}  # anchored on the real today — that's the point
    monkeypatch.setattr(
        insights, "store", types.SimpleNamespace(query_daily_bulk=lambda: bulk)
    )
    out = insights.compute()
    assert not any("275" in i["detail"] for i in out)
    assert not any(i["kind"] == "anomaly" and i["metric"] == "steps" for i in out)
