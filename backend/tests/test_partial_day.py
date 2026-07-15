"""Today's accumulating partials must never compete with complete days.

Pins the 2026-07-11 bug where 275 steps at 9 AM read as a 30-day record low:
INTERVAL metrics (and cardio-load) accumulate through the day, so analytics has
to drop today's row before comparing days, while point-in-time metrics keep it.
"""
from datetime import date, timedelta

from app import config
from app.briefing import _slim_sleep, _today_so_far
from app.config import REGISTRY_BY_NAME
from app.insights import complete_days


def _rows(values_by_days_back: dict[int, float]) -> list[dict]:
    """{days_back: value} → ascending day rows, 0 = today."""
    today = date.today()
    return [
        {"day": (today - timedelta(days=b)).isoformat(), "value": v, "unit": ""}
        for b, v in sorted(values_by_days_back.items(), key=lambda p: -p[0])
    ]


def test_accumulates_today_registry():
    accumulating = {"steps", "active-zone-minutes", "distance", "cardio-load"}
    complete_on_arrival = {"daily-heart-rate-variability", "daily-resting-heart-rate",
                           "sleep-duration", "sleep-score", "weight"}
    for name in accumulating:
        assert config.accumulates_today(REGISTRY_BY_NAME[name]), name
    for name in complete_on_arrival:
        assert not config.accumulates_today(REGISTRY_BY_NAME[name]), name


def test_complete_days_drops_todays_partial():
    cache = {
        "steps": _rows({2: 8200, 1: 9100, 0: 275}),
        "daily-heart-rate-variability": _rows({1: 48.0, 0: 52.0}),
    }
    out = complete_days(cache)
    assert [r["value"] for r in out["steps"]] == [8200, 9100]
    # Point-in-time metrics keep today: the value is already real.
    assert [r["value"] for r in out["daily-heart-rate-variability"]] == [48.0, 52.0]


def test_complete_days_without_today_row_is_untouched():
    cache = {"steps": _rows({3: 8000, 2: 8500, 1: 9000})}
    assert complete_days(cache)["steps"] == cache["steps"]


def test_complete_days_ignores_unregistered_metrics():
    cache = {"mystery-metric": _rows({1: 1.0, 0: 2.0})}
    assert complete_days(cache)["mystery-metric"] == cache["mystery-metric"]


def test_today_so_far_lists_only_accumulating_metrics():
    bulk = {
        "steps": _rows({1: 9000, 0: 4321}),
        "daily-heart-rate-variability": _rows({1: 48.0, 0: 52.0}),
    }
    block = _today_so_far(bulk)
    assert "PARTIAL" in block["note"]
    names = [m["metric"] for m in block["metrics"]]
    assert names == ["steps"]
    assert block["metrics"][0]["so_far_today"] == 4321


def test_slim_sleep_exposes_countable_recent_nights():
    nights = [{"day": f"2026-07-{d:02d}", "duration": 7.0 + d / 10, "score": 80 + d,
               "deep": 1.0, "rem": 1.5, "light": 4.0, "awake": 0.5}
              for d in range(1, 11)]
    slim = _slim_sleep({"nights": nights, "need_hours": 7.5})
    assert "nights" not in slim
    assert len(slim["recent_nights"]) == 7
    assert slim["recent_nights"][-1] == {"day": "2026-07-10", "duration": 8.0, "score": 90}
