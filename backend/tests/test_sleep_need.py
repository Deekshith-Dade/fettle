"""Personalized sleep need: median of your own valid nights, held to the NSF band.

Pins the artifact-night filter (sub-3h tracker fragments once minted ~5h of fake
debt) and the clamp that stops chronic restriction from becoming the target.
"""
from app.sleep_analysis import NEED_BAND, NEED_FALLBACK, NEED_MIN_NIGHTS, personal_need


def test_population_fallback_until_enough_nights():
    out = personal_need([6.5] * (NEED_MIN_NIGHTS - 1))
    assert out["source"] == "population"
    assert out["hours"] == NEED_FALLBACK
    assert out["median"] is None


def test_artifact_nights_do_not_count_toward_the_minimum():
    # 13 real nights + 5 sub-3h fragments: still not enough evidence to go personal.
    out = personal_need([7.5] * 13 + [2.0] * 5)
    assert out["source"] == "population"
    assert out["nights"] == 13


def test_personal_median_ignores_artifact_nights():
    real = [7.0] * 10 + [8.2] * 10          # median 7.6
    with_artifacts = [1.5, 1.5] + real       # unfiltered median would be 7.0
    out = personal_need(with_artifacts)
    assert out["source"] == "personal"
    assert out["hours"] == 7.6
    assert out["clamped"] is False


def test_chronic_short_sleep_is_clamped_up():
    out = personal_need([5.5] * 20)
    assert out["median"] == 5.5
    assert out["hours"] == NEED_BAND[0]
    assert out["clamped"] is True


def test_long_sleeper_is_clamped_down():
    out = personal_need([9.8] * 20)
    assert out["hours"] == NEED_BAND[1]
    assert out["clamped"] is True


def test_need_draws_on_the_recent_window_only():
    # 20 old 9h nights fall outside the 60-night window; the recent 60 define need.
    out = personal_need([9.0] * 20 + [7.5] * 60)
    assert out["hours"] == 7.5
    assert out["nights"] == 60
