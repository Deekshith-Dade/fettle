"""AI coach chat — bridges the dashboard to the opencode CLI.

Each turn shells out to `opencode run --format json --agent fettle-coach` and re-emits
its NDJSON part events as Server-Sent Events the chat UI can render live:

    meta  {conversation_id, title, model}     first, so a new chat gets its id at once
    tool  {name, label, input}                as the agent consults the MCP tools
    text  {text}                              each completed text part
    done  {message_id, tokens, model}         after the transcript is persisted
    error {message}

Conversation context lives in opencode's own session (`-s <id>` continues it), so we
never replay history through the model; our tables only store what the UI renders.
The models the picker offers come from `opencode models` — with a Zen account that is
exactly the free set, and every run is cost 0.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import chat_store
from .config import BASE_DIR

# opencode reads opencode.json + .opencode/agent/ from the working directory,
# so every subprocess runs from the repo root.
REPO_ROOT = BASE_DIR.parent
UPLOAD_DIR = BASE_DIR / "uploads"

AGENT = "fettle-coach"
DEFAULT_MODEL = "opencode/deepseek-v4-flash-free"  # proven tool-caller on the free tier
TURN_TIMEOUT = 240   # hard cap on one whole turn (multi-tool turns take ~10-40s)
LINE_TIMEOUT = 150   # max quiet time between events before we assume a hang
MAX_UPLOAD = 15 * 1024 * 1024

# Display tools render inline widgets in the chat instead of feeding the model data.
# The bridge spots them by prefix and emits a `widget` SSE event with the call's params;
# the frontend mounts the matching component (which fetches its own fresh data).
WIDGET_PREFIX = "fettle_show_"

# Friendly names for the MCP tools (opencode namespaces them as fettle_<tool>).
TOOL_LABELS = {
    "fettle_list_metrics": "Metric catalog",
    "fettle_get_summary": "Data summary",
    "fettle_get_metric": "Metric history",
    "fettle_get_intraday": "Intraday detail",
    "fettle_get_readiness": "Readiness engine",
    "fettle_get_insights": "Insights scan",
    "fettle_get_coach": "Coach engine",
    "fettle_get_benchmarks": "Peer benchmarks",
    "fettle_get_sleep": "Sleep analysis",
    "fettle_get_goals": "Goal tracker",
    "fettle_get_workouts": "Session log",
    "fettle_create_goal": "Goal created",
    "fettle_update_goal": "Goal updated",
    "fettle_delete_goal": "Goal removed",
    "fettle_remember": "Saved to memory",
    "fettle_recall": "Memory recall",
    "fettle_forget": "Memory removed",
}

# Tool calls that change stored goals — the UI busts its goals-widget cache on these.
GOAL_MUTATIONS = {"fettle_create_goal", "fettle_update_goal", "fettle_delete_goal"}

router = APIRouter(prefix="/api/chat")
chat_store.init_db()


def _opencode_bin() -> str:
    # uvicorn may not inherit the login PATH (launchd, IDEs) — fall back to the installer path.
    return shutil.which("opencode") or str(Path.home() / ".opencode" / "bin" / "opencode")


def _opencode_env() -> dict[str, str]:
    """Env for every opencode subprocess. opencode trusts $PWD over its real cwd when
    resolving the project — and uvicorn's inherited PWD is backend/, which silently
    breaks the checkout-relative MCP command paths in opencode.json (the coach loses
    all fettle_* tools). Pin PWD to match the cwd we spawn with."""
    return {**os.environ, "PWD": str(REPO_ROOT)}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


_ANSI = re.compile(r"\x1b\[[0-9;]*m")

def _plain(text: str) -> str:
    """CLI error output carries ANSI color codes — strip them before the UI sees it."""
    return _ANSI.sub("", text).strip()


def _title_from(message: str) -> str:
    first = message.strip().splitlines()[0]
    return first[:60] + ("…" if len(first) > 60 else "")


# --- models for the picker -----------------------------------------------------

_NAME_FIXES = {"deepseek": "DeepSeek", "glm": "GLM", "hy3": "HY3", "mimo": "MiMo",
               "v2.5": "V2.5", "v4": "V4"}

def _pretty(model_id: str) -> str:
    slug = model_id.split("/", 1)[-1].removesuffix("-free")
    words = [_NAME_FIXES.get(w, w.capitalize()) for w in slug.split("-")]
    return " ".join(words)


_ids_cache: tuple[float, list[str]] = (0.0, [])

def _available_ids() -> list[str]:
    """Model ids the local opencode account can run right now (10-min cache).
    Empty means the CLI call itself failed (opencode down / logged out) — no info."""
    global _ids_cache
    ts, cached = _ids_cache
    if cached and time.time() - ts < 600:
        return cached
    try:
        proc = subprocess.run(
            [_opencode_bin(), "models"], capture_output=True, text=True,
            timeout=30, cwd=str(REPO_ROOT), env=_opencode_env(),
        )
        ids = [ln.strip() for ln in proc.stdout.splitlines()
               if ln.strip().startswith("opencode/")]
    except (OSError, subprocess.TimeoutExpired):
        ids = []
    if ids:
        _ids_cache = (time.time(), ids)
    return ids


def resolve_model(requested: str | None = None) -> str:
    """The model a run should use. The free Zen lineup rotates ('limited-time beta'),
    so a stored/default id that has vanished must degrade to the first model the
    account still offers — not surface as a cryptic run failure. An empty catalog
    (CLI unreachable) returns the request unchanged and lets opencode report."""
    ids = _available_ids()
    want = requested or DEFAULT_MODEL
    if want in ids or not ids:
        return want
    return ids[0]


@router.get("/models")
def models() -> list[dict]:
    """Models the picker offers (with a Zen account: the free set)."""
    ids = _available_ids() or [DEFAULT_MODEL]  # keep the UI alive if the CLI is down
    rec = DEFAULT_MODEL if DEFAULT_MODEL in ids else ids[0]
    return [{"id": i, "label": _pretty(i), "recommended": i == rec} for i in ids]


# --- conversations ---------------------------------------------------------------

@router.get("/conversations")
def conversations() -> list[dict]:
    return chat_store.list_conversations()


@router.get("/conversations/{cid}")
def conversation(cid: int) -> dict:
    conv = chat_store.get_conversation(cid)
    if not conv:
        raise HTTPException(404, "No such conversation.")
    conv.pop("opencode_session_id", None)  # internal detail
    return {**conv, "messages": chat_store.list_messages(cid)}


class ConvPatch(BaseModel):
    title: str


@router.patch("/conversations/{cid}")
def conversation_rename(cid: int, patch: ConvPatch) -> dict:
    if not chat_store.get_conversation(cid):
        raise HTTPException(404, "No such conversation.")
    chat_store.rename_conversation(cid, patch.title.strip()[:80] or "Untitled")
    return {"ok": True}


@router.delete("/conversations/{cid}")
def conversation_delete(cid: int) -> dict:
    chat_store.delete_conversation(cid)
    return {"ok": True}


# --- attachments ------------------------------------------------------------------

@router.post("/attachments")
async def upload_attachment(file: UploadFile = File(...)) -> dict:
    """Stage a file for the next message; the id is passed back with the send."""
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (15 MB max).")
    UPLOAD_DIR.mkdir(exist_ok=True)
    ext = Path(file.filename or "").suffix[:12]
    fid = f"{uuid.uuid4().hex}{ext}"
    (UPLOAD_DIR / fid).write_bytes(data)
    return {"id": fid, "name": file.filename or fid}


def _resolve_attachment(fid: str) -> Path:
    # ids are server-generated filenames; basename() guards against path traversal.
    path = UPLOAD_DIR / Path(fid).name
    if not path.is_file():
        raise HTTPException(400, f"Unknown attachment '{fid}'.")
    return path


# --- the chat turn -----------------------------------------------------------------

class AttachmentRef(BaseModel):
    id: str
    name: str = ""


class ChatIn(BaseModel):
    message: str
    conversation_id: int | None = None
    model: str | None = None
    attachments: list[AttachmentRef] = []


@router.post("")
async def chat(body: ChatIn) -> StreamingResponse:
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "Empty message.")

    if body.conversation_id is not None:
        conv = chat_store.get_conversation(body.conversation_id)
        if not conv:
            raise HTTPException(404, "No such conversation.")
    else:
        conv = chat_store.create_conversation(_title_from(message), body.model or DEFAULT_MODEL)

    # Resolve against the live catalog — a rotated-out model falls back gracefully.
    model = await asyncio.to_thread(resolve_model, body.model or conv.get("model"))
    if model != conv.get("model"):
        chat_store.set_model(conv["id"], model)

    attach_paths = [_resolve_attachment(a.id) for a in body.attachments]
    attach_names = [a.name or a.id for a in body.attachments]

    return StreamingResponse(
        _turn(conv, message, model, attach_paths, attach_names),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _turn(
    conv: dict, message: str, model: str,
    attach_paths: list[Path], attach_names: list[str],
) -> AsyncIterator[str]:
    cid = conv["id"]
    chat_store.add_message(
        cid, "user", message,
        {"attachments": [{"name": n} for n in attach_names]} if attach_names else None,
    )
    yield _sse("meta", {"conversation_id": cid, "title": conv["title"], "model": model})

    # First attempt continues the stored opencode session; if that session is gone
    # (e.g. opencode's local DB was reset) and nothing streamed yet, retry fresh once.
    session_id = conv.get("opencode_session_id")
    attempts = [session_id, None] if session_id else [None]

    for attempt_session in attempts:
        texts: list[str] = []
        tools_used: list[dict] = []
        # Ordered text/widget blocks — preserves where each visual sits in the answer.
        blocks: list[dict] = []
        seen_calls: set[str] = set()
        tokens_total = 0
        emitted = False
        failure = ""

        cmd = [_opencode_bin(), "run", "--format", "json", "--agent", AGENT, "-m", model]
        if attempt_session:
            cmd += ["-s", attempt_session]
        else:
            cmd += ["--title", conv["title"][:60]]
        # --file is a greedy yargs array — use the =value form and fence the message
        # behind `--`, or the message itself gets parsed as a file path.
        for p in attach_paths:
            cmd.append(f"--file={p}")
        cmd += ["--", message]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=str(REPO_ROOT), env=_opencode_env(),
            )
        except OSError as exc:
            yield _sse("error", {"message": f"Could not launch opencode: {exc}"})
            return

        started = time.monotonic()
        rc: int | None = None
        try:
            assert proc.stdout is not None
            while True:
                if time.monotonic() - started > TURN_TIMEOUT:
                    failure = "The coach took too long and was stopped."
                    break
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), LINE_TIMEOUT)
                except asyncio.TimeoutError:
                    failure = "The coach stopped responding mid-turn."
                    break
                if not line:
                    break
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue  # banner/noise line

                sid = evt.get("sessionID")
                if sid and sid != conv.get("opencode_session_id"):
                    conv["opencode_session_id"] = sid
                    chat_store.set_session(cid, sid)

                etype = evt.get("type")
                part = evt.get("part") or {}
                if etype == "tool_use":
                    state = part.get("state") or {}
                    if state.get("status") != "completed":
                        continue  # only act on finished calls (skip errored/partial)
                    call_id = part.get("callID") or ""
                    if call_id and call_id in seen_calls:
                        continue
                    seen_calls.add(call_id)
                    emitted = True
                    name = part.get("tool", "")
                    params = state.get("input") or {}
                    if name.startswith(WIDGET_PREFIX):
                        widget = {"kind": name.removeprefix(WIDGET_PREFIX), "params": params}
                        blocks.append({"type": "widget", "widget": widget})
                        yield _sse("widget", widget)
                    else:
                        info = {
                            "name": name,
                            "label": TOOL_LABELS.get(name, name.replace("fettle_", "").replace("_", " ")),
                            "input": params,
                        }
                        tools_used.append(info)
                        yield _sse("tool", info)
                elif etype == "text":
                    text = part.get("text") or ""
                    if text:
                        emitted = True
                        texts.append(text)
                        blocks.append({"type": "text", "text": text})
                        yield _sse("text", {"text": text})
                elif etype == "step_finish":
                    tokens_total += int((part.get("tokens") or {}).get("total") or 0)
                elif etype == "error":
                    failure = str(part.get("error") or evt.get("error") or "model error")

            if rc is None:
                if proc.returncode is None and failure:
                    proc.kill()
                rc = await proc.wait()
            stderr_tail = ""
            if proc.stderr is not None:
                stderr_tail = _plain((await proc.stderr.read())[-400:].decode(errors="replace"))
        finally:
            if proc.returncode is None:
                proc.kill()

        has_widget = any(b["type"] == "widget" for b in blocks)
        if not failure and rc == 0 and (texts or has_widget):
            mid = chat_store.add_message(
                cid, "assistant", "\n\n".join(texts),
                {"tools": tools_used, "blocks": blocks, "model": model, "tokens": tokens_total},
            )
            yield _sse("done", {"message_id": mid, "tokens": tokens_total, "model": model})
            return

        # A stored session that no longer exists fails before emitting anything —
        # clear it and fall through to the fresh-session attempt.
        if attempt_session and not emitted:
            chat_store.set_session(cid, None)
            conv["opencode_session_id"] = None
            continue

        detail = _plain(failure) or stderr_tail or f"opencode exited {rc} without an answer"
        yield _sse("error", {"message": detail[:400]})
        return

    yield _sse("error", {"message": "Could not reach the coach — is opencode logged in?"})
