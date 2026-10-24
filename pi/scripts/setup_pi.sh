#!/usr/bin/env bash
# One-shot setup of a freshly flashed Raspberry Pi OS for Robert (idempotent).
# Run from the Mac after flashing (user pi, Wi-Fi, SSH key set in Imager):
#   PI=pi@<ip> SSH_OPTS="-o HostKeyAlias=robert.local" pi/scripts/setup_pi.sh
# Asks for the Pi's sudo password once. Then reboot the Pi and run
#   pi/scripts/setup_pi.sh audio      (needs the ReSpeaker driver loaded)
set -euo pipefail
PI="${PI:-pi@robert.local}"
SSH_OPTS="${SSH_OPTS:-}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STEP="${1:-system}"
ssh_pi() { ssh $SSH_OPTS "$PI" "$@"; }

if [ -z "${PW:-}" ]; then read -rsp "sudo password on the Pi: " PW; echo; fi

if [ "$STEP" = audio ]; then
  # Levels validated 2026-10-23 (see memory/README): mic +21 dB with HPF, output -12 dB.
  ssh_pi "PW='$PW' bash -s" <<'EOF'
    C=seeed2micvoicec
    amixer -q -c $C sset "Left PGA Mixer Line1L" on; amixer -q -c $C sset "Right PGA Mixer Line1R" on
    amixer -q -c $C sset PGA 42; amixer -q -c $C sset "ADC HPF Cut-off" "0.0045xFs","0.0045xFs"
    amixer -q -c $C sset PCM 103; amixer -q -c $C sset "HP DAC" 100%; amixer -q -c $C sset "Line DAC" 100%
    amixer -q -c $C sset HP 0; amixer -q -c $C sset Line 0
    printf '%s\n' "$PW" | sudo -S -p '' alsactl store && echo "audio levels stored"
EOF
  exit 0
fi

echo "== code + secrets"
ssh_pi 'mkdir -p ~/robert ~/robert-models/piper'
rsync -az --delete -e "ssh $SSH_OPTS" --exclude .venv --exclude __pycache__ --exclude .pytest_cache \
  --exclude .env "$ROOT/pi" "$ROOT/frontend" "$ROOT/ros" "$PI:robert/"
scp -q $SSH_OPTS "$ROOT/pi/.env" "$PI:robert/pi/.env"
ssh_pi 'chmod 600 ~/robert/pi/.env'

echo "== boot config: ReSpeaker + OV5647 camera"
ssh_pi "PW='$PW' bash -s" <<'EOF'
set -e
S() { printf '%s\n' "$PW" | sudo -S -p '' "$@"; }
[ -d ~/seeed-linux-dtoverlays ] || git clone -q --depth 1 https://github.com/Seeed-Studio/seeed-linux-dtoverlays.git ~/seeed-linux-dtoverlays
dtc -@ -I dts -O dtb -o /tmp/respeaker.dtbo ~/seeed-linux-dtoverlays/overlays/rpi/respeaker-2mic-v2_0-overlay.dts 2>/dev/null
S cp /tmp/respeaker.dtbo /boot/firmware/overlays/respeaker-2mic-v2_0.dtbo
if ! grep -q "^# Robert:" /boot/firmware/config.txt; then
  cp /boot/firmware/config.txt /tmp/config.new
  sed -i "s/^camera_auto_detect=1/camera_auto_detect=0/" /tmp/config.new
  cat >> /tmp/config.new <<'CFG'
# Robert: ReSpeaker 2-Mics Pi HAT V2.0 (TLV320AIC3104 @0x18)
dtparam=i2c_arm=on
dtparam=i2s=on
dtoverlay=respeaker-2mic-v2_0
# Robert: Freenove OV5647 camera (not auto-detected)
dtoverlay=ov5647
CFG
  S cp /boot/firmware/config.txt /boot/firmware/config.txt.bak-pre-robert
  S cp /tmp/config.new /boot/firmware/config.txt
  rm /tmp/config.new
fi
# Logs in RAM: fewer SD writes, so a power cut is less likely to corrupt the card.
S mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=30M\n' > /tmp/robert-journal.conf
S cp /tmp/robert-journal.conf /etc/systemd/journald.conf.d/robert.conf
EOF

echo "== python env (backend + voice)"
ssh_pi 'cd ~/robert/pi && { [ -d .venv ] || python3 -m venv .venv; } \
  && .venv/bin/pip install -q -r requirements.txt -r requirements-voice.txt \
  && .venv/bin/pip install -q --no-deps openwakeword \
  && .venv/bin/python -c "import openwakeword.utils as u; u.download_models([\"hey_jarvis\"])" >/dev/null 2>&1'
ssh_pi 'cd ~/robert-models/piper && for f in es_ES-davefx-medium.onnx es_ES-davefx-medium.onnx.json; do
  [ -s $f ] || curl -sSL -o $f "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/davefx/medium/$f"; done'

echo "== services"
ssh_pi "PW='$PW' bash -s" <<'EOF'
set -e
S() { printf '%s\n' "$PW" | sudo -S -p '' "$@"; }
for svc in robert robert-voice; do S cp ~/robert/pi/deploy/$svc.service /etc/systemd/system/; done
S systemctl daemon-reload
S systemctl enable robert robert-voice >/dev/null 2>&1
echo "services enabled (they start on the next boot)"
EOF
echo "Done. Reboot the Pi (sudo reboot), then: pi/scripts/setup_pi.sh audio"
