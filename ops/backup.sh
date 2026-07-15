#!/usr/bin/env bash
# Run one backup now — snapshots backend/health.db (+ token/credentials) to
# iCloud Drive, keeping the newest 14. Thin wrapper over `cli.py backup`;
# the logic lives in backend/app/backup.py. Override the destination with
# FETTLE_BACKUP_DIR. The nightly launchd job (ops/install-backup.sh) calls
# cli.py directly.
#
# Restore (stop the backend first):
#   gunzip -c "…/fettle-backups/health-YYYY-MM-DD.db.gz" > backend/health.db
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO/backend"
exec .venv/bin/python cli.py backup
