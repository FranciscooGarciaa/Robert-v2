#!/usr/bin/env bash
# Sync backend + frontend to the Pi and restart the service.
#   PI=pi@robert.local pi/scripts/deploy.sh
set -euo pipefail
PI="${PI:-pi@robert.local}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

rsync -az --delete \
  --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' --exclude '.env' \
  "$ROOT/pi" "$ROOT/frontend" "$PI:robert/"

ssh "$PI" 'cd robert/pi && { [ -d .venv ] || python3 -m venv .venv; } && .venv/bin/pip install -q -r requirements.txt'
ssh -t "$PI" 'sudo systemctl restart robert && systemctl --no-pager --lines=5 status robert'
