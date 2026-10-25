#!/usr/bin/env bash
# Get Robert ready to leave home, from the Mac (robot on, at home on home-wifi):
#   PW=<pi sudo password> pi/scripts/street_ready.sh
# 1. OTA-flash the ESP32 (secrets.h knows "My phone hotspot" + home-wifi)
# 2. SD unprotect -> reboot -> deploy -> add the iPhone hotspot to the Pi -> protect -> reboot
# 3. Health check
set -euo pipefail
PI="${PI:-pi@robert.local}"
SSH_OPTS="${SSH_OPTS:--o HostKeyAlias=robert.local}"
HOTSPOT="${HOTSPOT:-My phone hotspot}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [ -z "${PW:-}" ]; then read -rsp "sudo password on the Pi: " PW; echo; fi
export PW PI SSH_OPTS
ssh_pi() { ssh $SSH_OPTS "$PI" "$@"; }
wait_pi() {
  sleep 8
  until ssh $SSH_OPTS -o BatchMode=yes -o ConnectTimeout=3 "$PI" 'systemctl is-active -q robert' 2>/dev/null; do sleep 3; done
}

echo "== 1/4 ESP32 firmware over the air"
TOKEN=$(sed -n 's/.*CONTROL_TOKEN = "\(.*\)";/\1/p' "$ROOT/firmware/robert_motor/secrets.h")
BUILD=$(mktemp -d)
arduino-cli compile --fqbn esp32:esp32:esp32 --output-dir "$BUILD" "$ROOT/firmware/robert_motor" >/dev/null
python3 ~/Library/Arduino15/packages/esp32/hardware/esp32/3.3.12/tools/espota.py \
  -i robert-motor.local -p 3232 -a "$TOKEN" -f "$BUILD/robert_motor.ino.bin" | tail -1
rm -rf "$BUILD"

echo "== 2/4 SD writable"
"$ROOT/pi/scripts/setup_pi.sh" unprotect >/dev/null
ssh_pi "printf '%s\n' '$PW' | sudo -S -p '' reboot" || true
wait_pi

echo "== 3/4 code + street Wi-Fi"
"$ROOT/pi/scripts/deploy.sh" >/dev/null 2>&1
HOT_PW=$(security find-generic-password -D "AirPort network password" -a "$HOTSPOT" -w)
ssh_pi "PW='$PW' SSID='$HOTSPOT' KEY='$HOT_PW' bash -s" <<'EOF'
S() { printf '%s\n' "$PW" | sudo -S -p '' "$@"; }
S nmcli connection delete "$SSID" >/dev/null 2>&1 || true
S nmcli connection add type wifi con-name "$SSID" ssid "$SSID" wifi-sec.key-mgmt wpa-psk \
  wifi-sec.psk "$KEY" connection.autoconnect yes connection.autoconnect-priority 20 >/dev/null
echo "Pi knows: $(nmcli -t -f NAME connection show | grep -v '^lo$' | tr '\n' ' ')"
EOF
unset HOT_PW
"$ROOT/pi/scripts/setup_pi.sh" protect >/dev/null
ssh_pi "printf '%s\n' '$PW' | sudo -S -p '' reboot" || true
wait_pi

echo "== 4/4 health"
ssh_pi 'until journalctl -u robert-voice -b --no-pager | grep -q "voice ready"; do sleep 2; done
echo "root: $(findmnt -n -o FSTYPE /)  power: $(vcgencmd get_throttled)"
systemctl is-active robert robert-voice robert-vision | tr "\n" " "; echo
curl -s localhost:8000/api/state | python3 -c "import json,sys; d=json.load(sys.stdin); print(\"esp32\", d[\"esp32\"][\"connected\"], \"lidar\", d[\"lidar\"][\"scan_hz\"], \"Hz\")"'
echo "Ready. In the street: iPhone hotspot ON with 'Maximize Compatibility', PWA at http://robert.local:8000"
