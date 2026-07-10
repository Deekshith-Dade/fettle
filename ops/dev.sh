#!/usr/bin/env bash
# Run both fettle servers — backend :8400, frontend :3400. Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -x backend/.venv/bin/python ] || { echo "No backend venv yet — run ops/bootstrap.sh first."; exit 1; }
[ -d frontend/node_modules ] || { echo "No frontend deps yet — run ops/bootstrap.sh first."; exit 1; }

# python -m uvicorn (not the uvicorn script): console-script shebangs die when a venv
# is rebuilt over a moved checkout; the module form only needs a working python.
# --host :: serves IPv4 AND IPv6. An IPv4-only bind breaks Safari, which resolves
# `localhost` to ::1 first: the page loads (Next binds both) but every API call fails.
( cd backend && exec .venv/bin/python -m uvicorn app.main:app --reload --host :: --port 8400 ) &
BACK=$!
( cd frontend && exec npm run dev -- -p 3400 ) &
FRONT=$!
trap 'kill "$BACK" "$FRONT" 2>/dev/null' INT TERM EXIT

printf '\n  fettle → http://localhost:3400\n\n'
if command -v open >/dev/null 2>&1; then ( sleep 3 && open http://localhost:3400 ) & fi

wait
