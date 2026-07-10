#!/usr/bin/env bash
# fettle bootstrap — one command from fresh clone to runnable app.
#
#   ops/bootstrap.sh
#
# Creates the backend virtualenv, installs Python + npm dependencies, and points
# you at ops/dev.sh. Safe to re-run; it only does what's missing.
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n\033[1m› %s\033[0m\n' "$*"; }

command -v python3 >/dev/null 2>&1 || { echo "python3 not found — install Python 3.11+ first."; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "npm not found — install Node 18+ first."; exit 1; }

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Python 3.11+ required (found $(python3 -V 2>&1))."
  exit 1
fi
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)'; then
  echo "note: $(python3 -V 2>&1) is very new — if pip install fails building wheels"
  echo "      (pydantic-core), use Python 3.12 or 3.13 for the venv instead."
fi

say "backend — virtualenv + dependencies"
[ -d backend/.venv ] || python3 -m venv backend/.venv
backend/.venv/bin/pip install --quiet --upgrade pip
backend/.venv/bin/pip install --quiet -r backend/requirements.txt

say "frontend — npm dependencies"
( cd frontend && npm install --no-fund --no-audit )

say "done"
cat <<'EOF'

  Start the app:      ops/dev.sh
  Then open:          http://localhost:3400
  First run?          The app walks you through connecting Google — ~10 minutes.

  Optional extras (any time later):
    ops/install-sync.sh     background sync every 6 hours (macOS launchd)
    README → "AI coach"     free local LLM coach via the opencode CLI

EOF
