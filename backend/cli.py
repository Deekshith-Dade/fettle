"""fettle command line.

    python cli.py auth              # browser OAuth, stores token.json
    python cli.py sync              # sync all registered data types
    python cli.py sync steps sleep  # sync only specific types
    python cli.py status            # show per-type watermarks
    python cli.py backup            # snapshot health.db (+tokens) to iCloud Drive
"""
from __future__ import annotations

import sys
import webbrowser
from datetime import date

from app import auth, store, sync
from app.config import REGISTRY


def cmd_auth() -> int:
    url, _ = auth.build_authorization_url()
    print("Opening browser for Google consent…")
    print("If it doesn't open, visit:\n  " + url + "\n")
    webbrowser.open(url)
    from app.config import settings
    print(
        f"After approving, your browser lands on the callback URL "
        f"({settings.oauth_redirect_uri}?...).\n"
        "Start the API first (`uvicorn app.main:app --port 8400`) so it can capture the "
        "code,\nOR paste the full redirected URL here:"
    )
    redirected = input("> ").strip()
    if redirected:
        auth.exchange_code(redirected)
        print("Token stored ✅")
    return 0


def cmd_sync(names: list[str]) -> int:
    try:
        types = sync.resolve_types(names or None)
        report = sync.run_sync(types)
    except auth.TokenExpiredError as exc:
        print(f"⚠️  {exc}", file=sys.stderr)
        return 2
    for r in report.results:
        if r.error:
            print(f"  ✗ {r.data_type}: {r.error}")
        else:
            print(f"  ✓ {r.data_type}: {r.daily_rows} daily, {r.intraday_rows} intraday")
    print(f"\nDone. {report.total_rows} rows total. ok={report.ok}")

    # Refresh the LLM daily briefing off the fresh data — best-effort: the sync's
    # success must never depend on the model being reachable.
    try:
        from app import briefing
        b = briefing.generate()
        if b:
            print(f"Briefing: {b['headline']}")
        if date.today().weekday() == 6:  # Sunday: close out the week too
            w = briefing.generate_weekly()
            if w:
                print(f"Weekly retro: {w['headline']}")
    except Exception as exc:  # noqa: BLE001
        print(f"  (briefing skipped: {exc})", file=sys.stderr)

    # Reach out only when something needs attention: token dying, vitals drifting
    # together, or a defended goal streak breaking. Deduped in app/notify.py.
    try:
        from app import notify
        for n in notify.check_and_send():
            print(f"Notified: {n['title']}")
    except Exception as exc:  # noqa: BLE001
        print(f"  (notify skipped: {exc})", file=sys.stderr)

    return 0 if report.ok else 1


def cmd_backup() -> int:
    # Without an iCloud-Drive TCC grant, launchd runs hang inside sqlite's open()
    # (which retries EINTR forever), and a hung job would block every later run.
    # A hard SIGALRM (default action: die) is the only guard that works there —
    # "starting" with no "✓" in the log is the tell. Fix: kickstart the job while
    # at the screen and click Allow on the macOS prompt.
    import signal
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.alarm(600)
    print("starting backup…", flush=True)
    from app import backup
    try:
        out = backup.run()
    except Exception as exc:  # noqa: BLE001 — the launchd log needs the reason
        print(f"✗ backup failed: {exc}", file=sys.stderr)
        return 1
    finally:
        signal.alarm(0)
    size_mb = out.stat().st_size / 1e6
    print(f"✓ backed up {size_mb:.1f} MB → {out}")
    return 0


def cmd_status() -> int:
    store.init_db()
    rows = store.sync_status()
    if not rows:
        print("No syncs yet. Registered types:")
        for dt in REGISTRY:
            print(f"  - {dt.api_name} ({dt.label})")
        return 0
    for r in rows:
        print(f"  {r['data_type']:<28} {r['kind']:<9} up to {r['last_day']}  "
              f"(synced {r['last_sync_at']})")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd, rest = argv[0], argv[1:]
    if cmd == "auth":
        return cmd_auth()
    if cmd == "sync":
        return cmd_sync(rest)
    if cmd == "status":
        return cmd_status()
    if cmd == "backup":
        return cmd_backup()
    print(f"Unknown command '{cmd}'.\n{__doc__}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
