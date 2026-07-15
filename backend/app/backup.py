"""Snapshot fettle's irreplaceable state to iCloud Drive.

health.db is the only copy of coach memories, chat history, goals, and briefing
history — none of it can be re-pulled from Google. Each run takes a consistent
snapshot with VACUUM INTO (safe while the API or a sync is writing), verifies it
with PRAGMA integrity_check, gzips it under a date stamp, and prunes beyond KEEP.
token.json / credentials.json ride along so a restored checkout can sync again
immediately.

Restore (stop the backend first):
    gunzip -c "…/fettle-backups/health-YYYY-MM-DD.db.gz" > backend/health.db
"""
from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
from datetime import date
from pathlib import Path

from .config import settings

DEFAULT_DEST = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/fettle-backups"
KEEP = 14


def run(db: Path | None = None, dest: Path | None = None, keep: int = KEEP) -> Path:
    """Back up `db` into `dest`, returning the snapshot path. Raises on any failure —
    the launchd log must show a loud error, never a silent half-backup."""
    db = db or settings.db_file
    dest = Path(os.environ.get("FETTLE_BACKUP_DIR") or dest or DEFAULT_DEST)
    if not db.exists():
        raise FileNotFoundError(f"no database at {db} — nothing to back up")
    dest.mkdir(parents=True, exist_ok=True)

    stamp = date.today().isoformat()  # one snapshot per day; a rerun overwrites it
    tmp = dest / f".health-{stamp}.db.tmp"
    out = dest / f"health-{stamp}.db.gz"

    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(db, timeout=60)  # wait out a writing sync rather than fail
    try:
        conn.execute("VACUUM INTO ?", (str(tmp),))
    finally:
        conn.close()
    try:
        check = sqlite3.connect(tmp)
        try:
            ok = check.execute("PRAGMA integrity_check;").fetchone()[0]
        finally:
            check.close()
        if ok != "ok":
            raise RuntimeError(f"integrity check failed on snapshot: {ok}")
        partial = out.with_suffix(out.suffix + ".partial")
        with open(tmp, "rb") as src, gzip.open(partial, "wb") as gz:
            shutil.copyfileobj(src, gz)
        partial.replace(out)
    finally:
        tmp.unlink(missing_ok=True)

    for extra in (settings.token_file, settings.credentials_file):
        if extra.exists():
            shutil.copy2(extra, dest / extra.name)

    # Date-stamped names sort chronologically; drop everything beyond the newest `keep`.
    snapshots = sorted(dest.glob("health-*.db.gz"))
    for old in snapshots[:-keep] if keep > 0 else []:
        old.unlink()

    return out
