#!/usr/bin/env bash
# Sync backend + frontend to the Pi and restart the service.
#   PI=<user>@robert.local pi/scripts/deploy.sh      (or set PI once in pi/scripts/local.env)
# If your computer cannot resolve robert.local, use the IP and keep host-key checks:
#   PI=<user>@<ip> SSH_OPTS="-o HostKeyAlias=robert.local" pi/scripts/deploy.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
[ -f "$ROOT/pi/scripts/local.env" ] && . "$ROOT/pi/scripts/local.env"   # your PI, SSH_OPTS, HOTSPOT (gitignored)
PI="${PI:-pi@robert.local}"
SSH_OPTS="${SSH_OPTS:-}"

rsync -az --delete -e "ssh $SSH_OPTS" \
  --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' --exclude '.env' --exclude 'robert-ca.key' --exclude 'robert-ca.srl' \
  "$ROOT/pi" "$ROOT/frontend" "$ROOT/ros" "$PI:robert/"

ssh $SSH_OPTS "$PI" 'cd robert/pi && { [ -d .venv ] || python3 -m venv .venv; } && .venv/bin/pip install -q -r requirements.txt -r requirements-voice.txt && .venv/bin/pip install -q --no-deps openwakeword'
# The service runs as our user with Restart=always: killing it restarts it
# with the new code, no sudo needed.
ssh $SSH_OPTS "$PI" 'for svc in robert robert-voice robert-vision; do
  pid=$(systemctl show -p MainPID --value $svc 2>/dev/null); [ "${pid:-0}" != 0 ] && kill $pid
done; sleep 4; systemctl --no-pager --lines=3 status robert robert-voice robert-vision'
