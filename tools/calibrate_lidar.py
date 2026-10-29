#!/usr/bin/env python3
"""Calibrate how the LiDAR sits on Robert, through the Pi backend (run on the Mac).

Order matters: mask first (what the robot hides from itself), then the frame.

  1. Self-mask: robot in open space (>= 1 m clear). Capture 3 times, turning or
     moving the robot by hand ~90 deg between captures, then build:
        pi/.venv/bin/python tools/calibrate_lidar.py mask-capture     (x3)
        pi/.venv/bin/python tools/calibrate_lidar.py mask-build
  2. Front: a box ~30 cm straight in front of the HC-SR04:
        pi/.venv/bin/python tools/calibrate_lidar.py front
  3. Direction: the same box ~30 cm on the robot's LEFT side:
        pi/.venv/bin/python tools/calibrate_lidar.py left
  4. Check: pi/.venv/bin/python tools/calibrate_lidar.py show

Each step updates pi/.env (Mac and Pi) or pi/lidar_mask.json and restarts the
backend. Pass --pi user@ip and --ssh-opts if robert.local does not resolve.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pi"))

from robert.config import Settings  # noqa: E402
from robert.nav import calib  # noqa: E402

ENV = ROOT / "pi" / ".env"
MASK = ROOT / "pi" / "lidar_mask.json"
CAPTURES = ROOT / "pi" / ".lidar_mask_captures.json"  # scratch, gitignored via .gitignore


def api(args, path: str) -> dict:
    with urllib.request.urlopen(f"http://{args.host}/{path}", timeout=3) as r:
        return json.load(r)


def fresh_scan(args) -> list[int]:
    first = api(args, "api/lidar/scan")["bins"]
    while True:
        time.sleep(0.05)
        bins = api(args, "api/lidar/scan")["bins"]
        if bins is None:
            raise SystemExit("no LiDAR data")
        if bins != first:
            return bins


def ssh(args, *cmd: str) -> None:
    subprocess.run(["ssh", *args.ssh_opts.split(), args.pi, *cmd], check=True)


def scp(args, src: Path, dst: str) -> None:
    subprocess.run(["scp", "-q", *args.ssh_opts.split(), str(src), f"{args.pi}:{dst}"], check=True)


def restart_backend(args) -> None:
    ssh(args, "kill $(systemctl show -p MainPID --value robert)")
    for _ in range(40):  # wait until it serves scans again
        time.sleep(0.5)
        try:
            if api(args, "api/lidar/scan")["bins"]:
                return
        except OSError:
            pass
    print("warning: backend did not come back within 20 s")


def set_env(args, **values) -> None:
    text = ENV.read_text() if ENV.is_file() else ""
    for key, value in values.items():
        line = f"{key}={value}"
        text, n = re.subn(rf"^{key}=.*$", line, text, flags=re.M)
        if not n:
            text += ("" if text.endswith("\n") or not text else "\n") + line + "\n"
    ENV.write_text(text)
    scp(args, ENV, "robert/pi/.env")
    print("set " + ", ".join(f"{k}={v}" for k, v in values.items()))
    restart_backend(args)


def settings() -> Settings:
    return Settings.from_env(ENV)


def current_mask():
    s = settings()
    return calib.load_mask(MASK, s.lidar_offset_deg, s.lidar_clockwise)


def cmd_mask_capture(args) -> None:
    caps = json.loads(CAPTURES.read_text()) if CAPTURES.is_file() else []
    caps.append(fresh_scan(args))
    CAPTURES.write_text(json.dumps(caps))
    print(f"capture {len(caps)} saved. Move/turn the robot ~90 deg and capture again"
          if len(caps) < 3 else f"capture {len(caps)} saved: run mask-build")


def cmd_mask_build(args) -> None:
    caps = json.loads(CAPTURES.read_text()) if CAPTURES.is_file() else []
    if len(caps) < 3:
        raise SystemExit(f"need 3 captures, have {len(caps)}")
    mask = calib.self_mask(caps)
    runs = calib.sectors(mask)
    print(f"robot hides {int(mask.sum())} deg: sectors {runs}")
    if mask.sum() >= calib.BIG_BLIND_DEG:
        print("WARNING: that is a large blind area. Something on the robot covers the LiDAR;"
              " autonomy will not drive into it. Raising the LiDAR or moving that part fixes it.")
    s = settings()
    calib.save_mask(mask, MASK, s.lidar_offset_deg, s.lidar_clockwise)
    CAPTURES.unlink()
    scp(args, MASK, "robert/pi/lidar_mask.json")
    restart_backend(args)
    print("mask saved to pi/lidar_mask.json (commit it)")


def cmd_front(args) -> None:
    s = settings()
    b = calib.nearest_bin(fresh_scan(args), ignore=current_mask())
    off = calib.offset_fix(b, s.lidar_offset_deg)
    print(f"box seen at {b} deg (should be 0)")
    if b in (0, 359, 1):
        print("front already right")
        return
    set_env(args, LIDAR_OFFSET_DEG=round(off, 1))


def cmd_left(args) -> None:
    s = settings()
    b = calib.nearest_bin(fresh_scan(args), ignore=current_mask())
    off, cw = calib.direction_fix(b, s.lidar_offset_deg, s.lidar_clockwise)
    print(f"box on the left seen at {b} deg (left = {calib.LEFT_DEG})")
    if cw == s.lidar_clockwise:
        print("direction already right")
        return
    print("sensor is mirrored: flipping direction")
    set_env(args, LIDAR_CLOCKWISE=str(cw).lower(), LIDAR_OFFSET_DEG=round(off, 1))


def cmd_show(args) -> None:
    state = api(args, "api/state")
    print("clearance (m):", state["nav"]["clearance"])
    print("blind sectors:", state["nav"].get("blind"))
    print("nearest:", state["lidar"]["nearest_mm"], "mm at", state["lidar"]["nearest_deg"], "deg")
    print("HC-SR04 front:", state["ultrasonic"]["distance_cm"], "cm")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="robert.local:8000")
    ap.add_argument("--pi", default=os.environ.get("PI", "pi@robert.local"))
    ap.add_argument("--ssh-opts", default="")
    ap.add_argument("cmd", choices=["mask-capture", "mask-build", "front", "left", "show"])
    args = ap.parse_args()
    globals()["cmd_" + args.cmd.replace("-", "_")](args)


if __name__ == "__main__":
    main()
