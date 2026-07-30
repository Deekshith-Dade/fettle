"""Production entrypoint: uvicorn bound to loopback only.

The API is deliberately unreachable from the LAN and from raw tailnet IP:port.
Every consumer reaches it through this same machine:
  - the phone: https://<mac>.ts.net:8444, where Tailscale Serve terminates TLS
    and path-mounts /api and /auth onto 127.0.0.1:8400 (same-origin for the
    dashboard, so no CORS or mixed-content trouble);
  - the desk: http://localhost:8400 (IPv6-first clients fall back to IPv4
    loopback on their own).
The old hand-rolled dual-stack socket existed only so raw tailnet IPv4 could
reach uvicorn directly; with Serve fronting everything, plain 127.0.0.1 is
enough. Port from argv[1] or $FETTLE_PORT, default 8400.
"""
import os
import sys

import uvicorn

PORT = int(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("FETTLE_PORT", "8400"))

uvicorn.run("app.main:app", host="127.0.0.1", port=PORT)
