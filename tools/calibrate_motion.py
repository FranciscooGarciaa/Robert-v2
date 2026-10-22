#!/usr/bin/env python3
"""Calibrate the nav model against the real robot, through the Pi backend.

Uses the LiDAR as the measuring tape (no encoders). Robot ON THE FLOOR,
open space, a flat wall straight ahead; keep a hand near the XT60.

    python tools/calibrate_motion.py offset            # object right in front
    python tools/calibrate_motion.py forward --pwm 100 # >= 2 m from the wall
    python tools/calibrate_motion.py spin --pwm 100 --mps-per-pwm 0.0031

Prints the lines to put in pi/.env. Commands go through the normal motion
path (dead-man, collision guard, ESP32 watchdog), like the PWA.
"""

import argparse
import asyncio
import json
import statistics
import time
import urllib.request

from websockets.asyncio.client import connect


def get(args, path: str) -> dict:
    with urllib.request.urlopen(f"http://{args.host}/{path}", timeout=2) as r:
        return json.load(r)


def fresh_scan(args) -> list[int]:
    """A scan taken after this call (so it reflects where the robot is now)."""
    first = get(args, "api/lidar/scan")["bins"]
    while True:
        time.sleep(0.05)
        bins = get(args, "api/lidar/scan")["bins"]
        if bins is None:
            raise SystemExit("no LiDAR data")
        if bins != first:
            return bins


def front_m(bins: list[int], half_width_deg: int = 2) -> float:
    vals = [bins[i % 360] for i in range(-half_width_deg, half_width_deg + 1) if bins[i % 360]]
    if not vals:
        raise SystemExit("nothing straight ahead; face a wall")
    return statistics.median(vals) / 1000


async def drive(args, left: int, right: int, seconds: float) -> float:
    """Drive like the PWA (10 Hz re-send), then stop. Returns the time actually driven."""
    async with connect(f"ws://{args.host}/ws") as ws:
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            await ws.send(json.dumps({"type": "drive", "left": left, "right": right}))
            await asyncio.sleep(0.1)
        await ws.send(json.dumps({"type": "stop"}))
        driven = time.monotonic() - start
        while (state := json.loads(await ws.recv())).get("type") != "state":
            pass
        if state["nav"].get("limited_by"):
            print(f"warning: the collision guard limited the run: {state['nav']['limited_by']}")
    await asyncio.sleep(0.8)  # come to rest before the next scan
    return driven


def best_shift(before: list[int], after: list[int]) -> int:
    """Rotation (degrees, + = robot turned left) that best maps `before` onto `after`."""
    def cost(k: int) -> float:
        diffs = [abs(after[(i + k) % 360] - b) for i, b in enumerate(before)
                 if b and after[(i + k) % 360]]
        return statistics.mean(diffs) if len(diffs) > 60 else float("inf")
    k = min(range(360), key=cost)
    return k if k <= 180 else k - 360


def cmd_offset(args) -> None:
    degs = []
    for _ in range(10):
        degs.append(get(args, "api/state")["lidar"]["nearest_deg"])
        time.sleep(0.2)
    deg = statistics.median(d for d in degs if d is not None)
    fix = -deg if deg <= 180 else 360 - deg
    print(f"nearest object at {deg}° (clockwise from current front)")
    print(f"-> add {fix:+.0f} to LIDAR_OFFSET_DEG in pi/.env (then restart the service)")


def cmd_forward(args) -> None:
    before = front_m(fresh_scan(args))
    driven = asyncio.run(drive(args, args.pwm, args.pwm, args.seconds))
    after = front_m(fresh_scan(args))
    moved = before - after
    print(f"wall {before:.3f} m -> {after:.3f} m: moved {moved:.3f} m in {driven:.2f} s")
    if moved <= 0.02:
        raise SystemExit("did not move: PWM below the deadband? try a higher --pwm")
    print(f"MPS_PER_PWM={moved / driven / args.pwm:.5f}")


def cmd_spin(args) -> None:
    before = fresh_scan(args)
    driven = asyncio.run(drive(args, -args.pwm, args.pwm, args.seconds))  # turn left
    after = fresh_scan(args)
    deg = best_shift(before, after)
    print(f"turned {deg}° in {driven:.2f} s")
    if deg <= 3:
        raise SystemExit("barely turned (or turned right?): check the LiDAR offset/direction")
    w = deg * 3.14159265 / 180 / driven
    print(f"TRACK_EFF_M={2 * args.pwm * args.mps_per_pwm / w:.3f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="robert.local:8000")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("offset")
    f = sub.add_parser("forward")
    f.add_argument("--pwm", type=int, default=100)
    f.add_argument("--seconds", type=float, default=1.5)
    s = sub.add_parser("spin")
    s.add_argument("--pwm", type=int, default=100)
    s.add_argument("--seconds", type=float, default=1.0)
    s.add_argument("--mps-per-pwm", type=float, required=True)
    args = ap.parse_args()
    {"offset": cmd_offset, "forward": cmd_forward, "spin": cmd_spin}[args.cmd](args)


if __name__ == "__main__":
    main()
