"""The schedule's promises: the seed installs once, days merge template + log +
one-offs honestly, archived blocks keep their history, rollups count what happened,
and reminders fire only for upcoming, unreminded, not-done blocks."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from app import schedule, store

D = date(2026, 7, 22)


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(store.settings, "db_file", tmp_path / "test.db")
    store.init_db()
    schedule.ensure_seed()
    # Tests pin their dates to July 2026; seed rows are stamped with the real clock,
    # so backdate their effect windows to before every pinned date.
    with store._connect() as conn:
        conn.execute("UPDATE schedule_blocks SET starts_on='2026-07-01', created_at='2026-07-01'")


def _block(label_part: str) -> dict:
    return next(b for b in store.list_blocks() if label_part.lower() in b["label"].lower())


def test_seed_installs_once(tmp_db):
    assert len(store.list_blocks()) == len(schedule.DEFAULT_BLOCKS)
    schedule.ensure_seed()  # second call must not duplicate
    assert len(store.list_blocks()) == len(schedule.DEFAULT_BLOCKS)


def test_seed_survives_emptying(tmp_db):
    # Removing everything must NOT trigger a re-seed — an emptied schedule is a choice.
    for b in store.list_blocks():
        schedule.end_block(b["id"], date.today())
    schedule.ensure_seed()
    assert store.list_blocks() == []


def test_day_view_merges_log_and_oneoffs(tmp_db):
    gym = _block("gym")
    schedule.log(D, block_id=gym["id"], done=True, note="upper body")
    schedule.add_oneoff(D, "12:30", "Dentist")
    v = schedule.day_view(D)
    assert v["total"] == len(schedule.DEFAULT_BLOCKS) + 1
    assert v["done"] == 1
    gym_item = next(x for x in v["items"] if x["block_id"] == gym["id"])
    assert (gym_item["done"], gym_item["note"]) == (True, "upper body")
    dentist = next(x for x in v["items"] if x["oneoff"])
    assert dentist["time"] == "12:30"
    # Items sort by clock time — the dentist lands between morning and evening blocks.
    times = [x["time"] for x in v["items"]]
    assert times == sorted(times)


def test_log_updates_do_not_duplicate(tmp_db):
    wake = _block("wake")
    schedule.log(D, block_id=wake["id"], done=False)
    schedule.log(D, block_id=wake["id"], done=True)   # changed his mind
    schedule.log(D, block_id=wake["id"], note="7:20, close enough")
    v = schedule.day_view(D)
    items = [x for x in v["items"] if x["block_id"] == wake["id"]]
    assert len(items) == 1
    assert items[0]["done"] is True                    # the note didn't clear the mark
    assert items[0]["note"] == "7:20, close enough"


def test_unlogged_blocks_read_as_null_not_missed(tmp_db):
    v = schedule.day_view(D)
    assert all(x["done"] is None for x in v["items"])


def test_ended_block_gone_forward_kept_backward(tmp_db):
    light = _block("light session")
    schedule.log(D, block_id=light["id"], done=True)
    schedule.end_block(light["id"], date(2026, 7, 23))  # remove from the 23rd onward
    assert all(x["block_id"] != light["id"] for x in schedule.day_view(date(2026, 7, 23))["items"])
    on_d = next(x for x in schedule.day_view(D)["items"] if x["block_id"] == light["id"])
    assert on_d["done"] is True  # the past kept both the block and its log


def test_weekday_mask(tmp_db):
    wknd = schedule.create_block("08:30", "Long breakfast", days="650", starts_on="2026-07-01")
    assert wknd["days"] == "056"  # normalized — Sat/Sun (and a stray Mon kept sorted)
    sat, wed = date(2026, 7, 25), date(2026, 7, 22)
    assert any(x["block_id"] == wknd["id"] for x in schedule.day_view(sat)["items"])
    b56 = schedule.create_block("09:00", "Weekend ride", days="56", starts_on="2026-07-01")
    assert any(x["block_id"] == b56["id"] for x in schedule.day_view(sat)["items"])
    assert all(x["block_id"] != b56["id"] for x in schedule.day_view(wed)["items"])
    with pytest.raises(ValueError):
        schedule.create_block("09:00", "bad", days="7")
    with pytest.raises(ValueError):
        schedule.create_block("09:00", "bad", days="")


def test_effect_window(tmp_db):
    future = schedule.create_block("07:00", "New wake", starts_on="2026-08-01")
    assert all(x["block_id"] != future["id"] for x in schedule.day_view(D)["items"])
    assert any(x["block_id"] == future["id"] for x in schedule.day_view(date(2026, 8, 1))["items"])


def test_edit_from_splits_like_a_calendar(tmp_db):
    gym = _block("gym")
    schedule.log(date(2026, 7, 21), block_id=gym["id"], done=True, note="pull day")
    newer = schedule.edit_block_from(gym["id"], date(2026, 7, 22), time="19:00", days="01234")
    assert newer["id"] != gym["id"]
    # The 21st keeps the old version — 18:15, done, note intact.
    old_item = next(x for x in schedule.day_view(date(2026, 7, 21))["items"]
                    if x["block_id"] == gym["id"])
    assert (old_item["time"], old_item["done"], old_item["note"]) == ("18:15", True, "pull day")
    assert all(x["block_id"] != newer["id"] for x in schedule.day_view(date(2026, 7, 21))["items"])
    # The 22nd (a Wednesday) gets the new version only.
    new_item = next(x for x in schedule.day_view(D)["items"] if x["block_id"] == newer["id"])
    assert (new_item["time"], new_item["done"]) == ("19:00", None)
    assert all(x["block_id"] != gym["id"] for x in schedule.day_view(D)["items"])
    # …and on Saturday the weekday-only new version is absent.
    assert all(x["block_id"] != newer["id"] for x in schedule.day_view(date(2026, 7, 25))["items"])


def test_oneoff_delete_guards_template_entries(tmp_db):
    gym = _block("gym")
    entry = schedule.log(D, block_id=gym["id"], done=True)
    with pytest.raises(ValueError):
        schedule.remove_oneoff(entry["id"])
    oneoff = schedule.add_oneoff(D, "12:30", "Dentist")
    schedule.remove_oneoff(oneoff["id"])
    assert store.get_entry(oneoff["id"]) is None


def test_validation(tmp_db):
    with pytest.raises(ValueError):
        schedule.create_block("25:00", "bad time")
    with pytest.raises(ValueError):
        schedule.create_block("08:00", "   ")
    with pytest.raises(ValueError):
        schedule.create_block("08:00", "ok", color="plaid")
    with pytest.raises(ValueError):
        schedule.log(D, block_id=_block("gym")["id"])  # nothing to log


def test_month_view_counts(tmp_db):
    with store._connect() as conn:
        conn.execute("UPDATE schedule_blocks SET starts_on='2026-07-15', created_at='2026-07-15'")
    gym = _block("gym")
    wake = _block("wake")
    schedule.log(date(2026, 7, 21), block_id=gym["id"], done=True)
    schedule.log(date(2026, 7, 22), block_id=gym["id"], done=True)
    schedule.log(date(2026, 7, 22), block_id=wake["id"], done=False)
    weekend = schedule.create_block("09:00", "Weekend ride", days="56", starts_on="2026-07-15")
    m = schedule.month_view(2026, 7, today=D)
    d22 = next(x for x in m["days"] if x["date"] == "2026-07-22")
    assert (d22["done"], d22["future"]) == (1, False)  # wake was marked missed, not done
    d23 = next(x for x in m["days"] if x["date"] == "2026-07-23")
    assert d23["future"] is True
    gym_stat = next(s for s in m["block_stats"] if s["block_id"] == gym["id"])
    assert gym_stat["done"] == 2
    # Adherence denominators count the days the block actually occurred: every day
    # since its start for gym, only Sat 18 + Sun 19 for the weekend-only block.
    assert gym_stat["days"] == (D - date(2026, 7, 15)).days + 1
    wknd_stat = next(s for s in m["block_stats"] if s["block_id"] == weekend["id"])
    assert wknd_stat["days"] == 2


def test_due_reminders_window(tmp_db):
    now = datetime(2026, 7, 22, 18, 0)  # gym block is at 18:15
    due = schedule.due_reminders(now)
    assert [d[1] for d in due] == ["Gym or run — 18:15"]
    # Done → silent; remind flag off → silent even in-window.
    schedule.log(D, block_id=_block("gym")["id"], done=True)
    assert schedule.due_reminders(now) == []
    assert schedule.due_reminders(datetime(2026, 7, 22, 9, 25)) == []  # day work: remind=0
    # One-offs remind too.
    schedule.add_oneoff(D, "18:10", "Call the bank")
    titles = [d[1] for d in schedule.due_reminders(now)]
    assert titles == ["Call the bank — 18:10"]
    # Recurrence-aware: a weekend-only block is silent midweek, live on Saturday.
    schedule.create_block("18:10", "Weekend ride", days="56", starts_on="2026-07-01")
    assert all("Weekend ride" not in t for _, t, _ in schedule.due_reminders(now))
    sat_due = [t for _, t, _ in schedule.due_reminders(datetime(2026, 7, 25, 18, 0))]
    assert "Weekend ride — 18:10" in sat_due
