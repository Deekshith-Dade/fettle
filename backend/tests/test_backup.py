"""The backup snapshot must be a restorable database, and pruning must keep
exactly the newest KEEP snapshots (date-stamped names sort chronologically)."""
import gzip
import sqlite3

from app import backup


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE coach_memory (id INTEGER PRIMARY KEY, note TEXT)")
    conn.execute("INSERT INTO coach_memory (note) VALUES ('remember the squat PR')")
    conn.commit()
    conn.close()


def test_snapshot_roundtrips_and_prunes(tmp_path, monkeypatch):
    monkeypatch.delenv("FETTLE_BACKUP_DIR", raising=False)
    db = tmp_path / "health.db"
    _make_db(db)

    dest = tmp_path / "backups"
    dest.mkdir()
    for i in range(1, 16):  # 15 stale snapshots for the pruner to bite on
        (dest / f"health-2026-01-{i:02d}.db.gz").write_bytes(b"old")

    out = backup.run(db=db, dest=dest, keep=14)

    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(out.read_bytes()))
    conn = sqlite3.connect(restored)
    assert conn.execute("SELECT note FROM coach_memory").fetchone()[0] == "remember the squat PR"
    conn.close()

    kept = sorted(f.name for f in dest.glob("health-*.db.gz"))
    assert len(kept) == 14
    assert out.name in kept
    assert "health-2026-01-01.db.gz" not in kept  # the two oldest fell off
    assert "health-2026-01-02.db.gz" not in kept


def test_missing_database_raises(tmp_path):
    try:
        backup.run(db=tmp_path / "nope.db", dest=tmp_path / "backups")
    except FileNotFoundError as exc:
        assert "nothing to back up" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")
