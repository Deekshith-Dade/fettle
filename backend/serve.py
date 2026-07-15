"""Production entrypoint: uvicorn on a genuine dual-stack socket.

`uvicorn --host ::` binds IPv6-only on macOS (asyncio sets IPV6_V6ONLY), so the API
is reachable over ::1 but NOT over IPv4 — including the Tailscale 100.x address the
phone uses, which makes the page load but every fetch fail ("TypeError: Load failed").
Binding the socket ourselves with IPV6_V6ONLY=0 accepts BOTH families (127.0.0.1 and
Safari's ::1 at the desk, tailnet IPv4/IPv6 from the phone), matching how node serves
the frontend. Port from argv[1] or $FETTLE_PORT, default 8400.
"""
import os
import socket
import sys

import uvicorn

PORT = int(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("FETTLE_PORT", "8400"))

sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)  # accept IPv4 too
sock.bind(("::", PORT))
sock.listen(128)

uvicorn.Server(uvicorn.Config("app.main:app", fd=sock.fileno())).run()
