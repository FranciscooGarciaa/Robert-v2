#!/usr/bin/env bash
# Copy Robert's memory (diaries + memory.json) between the Pi and the Mac.
#   pi/scripts/pull_memory.sh          Pi -> Mac (pi/memory/, outside git)
#   pi/scripts/pull_memory.sh push     Mac -> Pi (after a reinstall, or to restore it)
# With the SD protected (read-only overlay) the memory on the Pi lives in RAM and is
# lost on reboot: pull it before switching off, push it back after.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
[ -f "$ROOT/pi/scripts/local.env" ] && . "$ROOT/pi/scripts/local.env"   # your PI, SSH_OPTS, HOTSPOT (gitignored)
PI="${PI:-pi@robert.local}"
SSH_OPTS="${SSH_OPTS:-}"
REMOTE="${MEMORY_DIR:-robert-memory}"
LOCAL="$ROOT/pi/memory"

case "${1:-pull}" in
  pull)
    mkdir -p "$LOCAL"
    rsync -az -e "ssh $SSH_OPTS" "$PI:$REMOTE/" "$LOCAL/"
    echo "memory copied to $LOCAL"
    python3 -c "import json,sys; m=json.load(open('$LOCAL/memory.json')); print('boots:', m['boots'], '| totals:', m['totals'])" 2>/dev/null || true
    ;;
  push)
    [ -f "$LOCAL/memory.json" ] || { echo "nothing to push in $LOCAL" >&2; exit 1; }
    rsync -az -e "ssh $SSH_OPTS" "$LOCAL/" "$PI:$REMOTE/"
    echo "memory restored on the Pi (restart the backend: pi/scripts/deploy.sh or kill it)"
    ;;
  *) echo "usage: $0 [pull|push]" >&2; exit 2 ;;
esac
