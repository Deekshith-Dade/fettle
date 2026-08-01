"""Thin client for the Matter controller sidecar (python-matter-server).

The sidecar (ops/com.fettle.matter.plist) owns the Matter fabric and speaks
WebSocket JSON on loopback :5580. It is launched through the FettleMatter.app
wrapper bundle — NOT the bare venv python — because macOS attributes Local
Network permission to the wrapper's bundle identity; raw python under launchd
gets silently denied (errno 65 on any LAN UDP) with no way to grant it.

Connections here are short-lived: one WebSocket per call batch. The engine
ticks every 30s and the sidecar holds the device subscriptions, so a
persistent socket would only add reconnect bookkeeping. First frame on
connect is the server-info dump; replies carry the request's message_id.

fastapi-free on purpose (mirrors schedule.py) so tests import it directly.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import websockets

from .config import settings


class MatterError(RuntimeError):
    """The sidecar answered with an error (or not at all)."""


class MatterSession:
    """A handful of commands over one WebSocket. Use as an async context manager."""

    def __init__(self, url: str | None = None):
        self.url = url or settings.matter_ws_url
        self._ws: Any = None
        self.server_info: dict = {}

    async def __aenter__(self) -> "MatterSession":
        self._ws = await websockets.connect(self.url, open_timeout=5, close_timeout=2)
        self.server_info = json.loads(await asyncio.wait_for(self._ws.recv(), 5))
        return self

    async def __aexit__(self, *exc) -> None:
        await self._ws.close()

    async def call(self, command: str, args: dict | None = None, timeout: float = 20) -> Any:
        mid = uuid.uuid4().hex[:12]
        await self._ws.send(json.dumps(
            {"message_id": mid, "command": command, "args": args or {}}))
        while True:
            data = json.loads(await asyncio.wait_for(self._ws.recv(), timeout))
            if data.get("message_id") != mid:
                continue  # events / other traffic
            if data.get("error_code") is not None:
                raise MatterError(data.get("details") or f"error {data['error_code']}")
            return data.get("result")


async def device_command(cluster_id: int, command_name: str, payload: dict | None = None,
                         node_id: int | None = None, endpoint_id: int | None = None) -> Any:
    async with MatterSession() as s:
        return await s.call("device_command", {
            "node_id": node_id or settings.lamp_node_id,
            "endpoint_id": endpoint_id if endpoint_id is not None else settings.lamp_endpoint,
            "cluster_id": cluster_id,
            "command_name": command_name,
            "payload": payload or {},
        })


async def read_attributes(paths: list[str], node_id: int | None = None) -> dict[str, Any]:
    """Read attribute paths ('endpoint/cluster/attribute') in one session.
    Returns {path: value}; a path the node doesn't answer just stays absent."""
    out: dict[str, Any] = {}
    async with MatterSession() as s:
        for p in paths:
            res = await s.call("read_attribute", {
                "node_id": node_id or settings.lamp_node_id, "attribute_path": p})
            if isinstance(res, dict):
                out.update(res)
    return out


async def ping() -> dict:
    """Reachability + fabric info (the first-frame server dump)."""
    async with MatterSession() as s:
        return s.server_info
