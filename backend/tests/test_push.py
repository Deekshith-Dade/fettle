"""Web push promises: subscriptions round-trip, garbage is rejected, gone endpoints
are pruned on send, and deliver() fans out to both channels without letting push
failures break the toast path."""
from __future__ import annotations

import json

import pytest
from pywebpush import WebPushException

from app import notify, push, store


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(store.settings, "db_file", tmp_path / "test.db")
    monkeypatch.setattr(push, "VAPID_FILE", tmp_path / "vapid.pem")
    store.init_db()


def _sub(endpoint: str) -> dict:
    return {"endpoint": endpoint, "keys": {"p256dh": "BPk", "auth": "abc"}}


def test_subscribe_roundtrip_and_validation(tmp_db):
    with pytest.raises(ValueError):
        push.subscribe({"endpoint": "https://x"})  # no keys
    push.subscribe(_sub("https://push.example/a"))
    push.subscribe(_sub("https://push.example/a"))  # same endpoint → upsert, not dup
    assert push.subscription_count() == 1
    assert push.unsubscribe("https://push.example/a") is True
    assert push.subscription_count() == 0


def test_send_all_prunes_gone_endpoints(tmp_db, monkeypatch):
    push.subscribe(_sub("https://push.example/alive"))
    push.subscribe(_sub("https://push.example/gone"))

    class GoneResp:
        status_code = 410

    def fake_webpush(subscription_info, **kw):
        if "gone" in subscription_info["endpoint"]:
            raise WebPushException("gone", response=GoneResp())
        return None

    monkeypatch.setattr(push, "webpush", fake_webpush)
    assert push.send_all("t", "b") == 1
    endpoints = [s["endpoint"] for s in store.list_push_subscriptions()]
    assert endpoints == ["https://push.example/alive"]


def test_vapid_public_key_is_stable(tmp_db):
    k1, k2 = push.public_key(), push.public_key()
    assert k1 == k2 and len(k1) > 60  # persisted once, reused after


def test_deliver_fans_out_and_survives_push_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(notify, "_send_macos", lambda t, b: calls.append(("mac", t)) or True)
    monkeypatch.setattr(push, "send_all",
                        lambda t, b, url="/", ttl=900: calls.append(("push", t, ttl)) or 2)
    assert notify.deliver("Gym or run — 18:15", "go", ttl=900) is True
    assert ("mac", "Gym or run — 18:15") in calls
    assert ("push", "Gym or run — 18:15", 900) in calls

    def boom(*a, **k):
        raise RuntimeError("relay down")
    monkeypatch.setattr(push, "send_all", boom)
    assert notify.deliver("t", "b") is True  # toast still counts
