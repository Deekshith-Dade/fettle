"""One-shot commissioning against a known IP (bypasses CHIP's multicast
discovery, which macOS Local Network privacy blocks in daemon contexts).

Usage: venv/bin/python commission.py <lamp-ip> [setup-pin]
"""
import asyncio
import json
import sys

import aiohttp

IP = sys.argv[1]
PIN = int(sys.argv[2]) if len(sys.argv) > 2 else 68904472  # decoded from 2097-524-2052


async def main() -> None:
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("http://127.0.0.1:5580/ws") as ws:
            info = json.loads((await ws.receive()).data)
            print("matter-server sdk", info.get("sdk_version"), "| commissioning", IP, flush=True)
            await ws.send_str(json.dumps({
                "message_id": "c1",
                "command": "commission_on_network",
                "args": {"setup_pin_code": PIN, "ip_addr": IP},
            }))
            while True:
                msg = await asyncio.wait_for(ws.receive(), 200)
                data = json.loads(msg.data)
                if data.get("message_id") == "c1":
                    if data.get("error_code") is not None:
                        print("FAILED:", json.dumps(data)[:500])
                        sys.exit(1)
                    node = data.get("result") or {}
                    print("SUCCESS node_id:", node.get("node_id"))
                    sys.exit(0)


asyncio.run(main())
