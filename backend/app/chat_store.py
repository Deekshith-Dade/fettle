"""Chat persistence — conversations and message transcripts for the coach UI.

The LLM's working context lives in opencode's own session (we continue it with
`opencode run -s <id>`); these tables only hold what the UI needs to render history:
conversation metadata and the message transcript (with tool-call/attachment parts).
Same SQLite file as the health data, so backups and WAL behaviour stay uniform.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .store import _connect

CHAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_conversations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    title               TEXT NOT NULL,
    opencode_session_id TEXT,              -- the LLM-side context; NULL until first reply
    model               TEXT,              -- last model used, e.g. opencode/deepseek-v4-flash-free
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role            TEXT NOT NULL,         -- 'user' | 'assistant'
    content         TEXT NOT NULL,         -- markdown
    parts           TEXT,                  -- JSON: {tools:[...], attachments:[...], model, tokens}
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_conv ON chat_messages (conversation_id, id);
"""


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(CHAT_SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_conversation(title: str, model: str | None) -> dict:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO chat_conversations (title, model, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (title, model, now, now),
        )
        cid = int(cur.lastrowid)
    return {"id": cid, "title": title, "opencode_session_id": None,
            "model": model, "created_at": now, "updated_at": now}


def list_conversations() -> list[dict]:
    with _connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, title, model, created_at, updated_at "
                "FROM chat_conversations ORDER BY updated_at DESC"
            ).fetchall()
        ]


def get_conversation(cid: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, title, opencode_session_id, model, created_at, updated_at "
            "FROM chat_conversations WHERE id=?",
            (cid,),
        ).fetchone()
    return dict(row) if row else None


def rename_conversation(cid: int, title: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE chat_conversations SET title=? WHERE id=?", (title, cid))


def delete_conversation(cid: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM chat_messages WHERE conversation_id=?", (cid,))
        conn.execute("DELETE FROM chat_conversations WHERE id=?", (cid,))


def set_session(cid: int, session_id: str | None) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE chat_conversations SET opencode_session_id=? WHERE id=?",
            (session_id, cid),
        )


def set_model(cid: int, model: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE chat_conversations SET model=? WHERE id=?", (model, cid))


def add_message(cid: int, role: str, content: str, parts: dict[str, Any] | None = None) -> int:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO chat_messages (conversation_id, role, content, parts, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, role, content, json.dumps(parts) if parts else None, now),
        )
        conn.execute("UPDATE chat_conversations SET updated_at=? WHERE id=?", (now, cid))
        return int(cur.lastrowid)


def list_messages(cid: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, role, content, parts, created_at FROM chat_messages "
            "WHERE conversation_id=? ORDER BY id",
            (cid,),
        ).fetchall()
    out = []
    for r in rows:
        m = dict(r)
        m["parts"] = json.loads(m["parts"]) if m["parts"] else None
        out.append(m)
    return out
