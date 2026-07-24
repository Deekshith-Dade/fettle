"""Web Push — reminders and alerts on his phone, self-hosted.

The PWA (served over the tailnet HTTPS proxy) subscribes in the browser; the
subscription lands in sqlite; `send_all` delivers through each browser's own push
relay (Apple/Google), with the payload encrypted end-to-end by pywebpush — the
relay never sees the text, and delivery works even when the phone is off the
tailnet. VAPID keys are generated once on first use and live next to the DB
(gitignored, like the OAuth token).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from py_vapid import Vapid02, b64urlencode
from pywebpush import WebPushException, webpush

from . import store
from .config import BASE_DIR

VAPID_FILE = BASE_DIR / "vapid_private.pem"
_CLAIM_SUB = "mailto:dadedeekshith@gmail.com"


def _vapid() -> Vapid02:
    if VAPID_FILE.exists():
        return Vapid02.from_file(str(VAPID_FILE))
    v = Vapid02()
    v.generate_keys()
    v.save_key(str(VAPID_FILE))
    VAPID_FILE.chmod(0o600)
    return v


def public_key() -> str:
    """The applicationServerKey the browser subscribes with (raw P-256 point, b64url)."""
    from cryptography.hazmat.primitives import serialization
    raw = _vapid().public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return b64urlencode(raw)


def subscribe(subscription: dict) -> None:
    endpoint = (subscription or {}).get("endpoint")
    if not endpoint or not (subscription.get("keys") or {}).get("p256dh"):
        raise ValueError("Not a web-push subscription (endpoint/keys missing).")
    store.add_push_subscription(endpoint, json.dumps(subscription))


def unsubscribe(endpoint: str) -> bool:
    return store.delete_push_subscription(endpoint)


def subscription_count() -> int:
    return len(store.list_push_subscriptions())


def send_all(title: str, body: str, url: str = "/", ttl: int = 900) -> int:
    """Push {title, body, url} to every subscription. Short TTLs keep time-sensitive
    reminders from arriving stale hours later. Gone subscriptions (404/410) are
    pruned; other failures are left alone (transient network, relay hiccup)."""
    subs = store.list_push_subscriptions()
    if not subs:
        return 0
    _vapid()  # ensure the key exists before signing
    payload = json.dumps({"title": title, "body": body, "url": url,
                          "at": datetime.now(timezone.utc).isoformat()})
    delivered = 0
    for s in subs:
        try:
            webpush(
                subscription_info=json.loads(s["subscription"]),
                data=payload,
                vapid_private_key=str(VAPID_FILE),
                vapid_claims={"sub": _CLAIM_SUB},
                ttl=ttl,
                timeout=10,
            )
            store.touch_push_subscription(s["endpoint"])
            delivered += 1
        except WebPushException as exc:
            code = getattr(exc.response, "status_code", None)
            if code in (404, 410):
                store.delete_push_subscription(s["endpoint"])
        except Exception:  # noqa: BLE001 — one bad endpoint must not stop the rest
            pass
    return delivered
