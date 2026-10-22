#!/usr/bin/env python3
"""Bench client for the ESP32 motor controller (runs on the Mac, no Pi needed).

WHEELS OFF THE GROUND for every command that moves motors.

    python tools/esp32_cli.py monitor
    python tools/esp32_cli.py drive 80 80 --seconds 2
    python tools/esp32_cli.py bench            # handoff section 49 sequence
    python tools/esp32_cli.py watchdog         # move, then go silent
    python tools/esp32_cli.py garbage          # malformed input must not move

Token: --token or ESP32_TOKEN env var (must match firmware secrets.h).
"""

import argparse
import asyncio
import json
import os
import time

from websockets.asyncio.client import connect


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


async def open_controller(args):
    ws = await connect(f"ws://{args.host}:{args.port}/", open_timeout=5)
    await ws.send(json.dumps({"type": "hello", "v": 1, "token": args.token}))
    ack = json.loads(await asyncio.wait_for(ws.recv(), 3))
    if ack.get("type") != "hello_ack":
        raise SystemExit(f"handshake rejected: {ack}")
    log(f"connected: {ack}")
    return ws


async def printer(ws, quiet_telemetry=False):
    async for raw in ws:
        msg = json.loads(raw)
        if msg.get("type") == "telemetry":
            if quiet_telemetry:
                continue
            log(
                f"dist={msg['distance_cm']} obst={msg['obstacle']} "
                f"target=({msg['left_target']},{msg['right_target']}) "
                f"out=({msg['left_out']},{msg['right_out']}) "
                f"failsafe={msg['failsafe']} rssi={msg['wifi_rssi']}"
            )
        else:
            log(f"<< {msg}")


async def hold(ws, left: int, right: int, seconds: float, hz: float = 10):
    """Send the same move at `hz` for `seconds` (acts as the heartbeat)."""
    log(f">> move L={left} R={right} for {seconds}s")
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        await ws.send(json.dumps({"type": "move", "left": left, "right": right}))
        await asyncio.sleep(1 / hz)


async def cmd_monitor(args):
    ws = await open_controller(args)
    task = asyncio.create_task(printer(ws))
    try:
        while True:
            await ws.send(json.dumps({"type": "heartbeat"}))
            await asyncio.sleep(0.2)
    finally:
        task.cancel()


async def cmd_drive(args):
    ws = await open_controller(args)
    task = asyncio.create_task(printer(ws))
    await hold(ws, args.left, args.right, args.seconds)
    await ws.send(json.dumps({"type": "stop"}))
    await asyncio.sleep(0.5)
    task.cancel()
    await ws.close()


async def cmd_bench(args):
    ws = await open_controller(args)
    task = asyncio.create_task(printer(ws, quiet_telemetry=True))
    s = args.speed
    steps = [
        ("forward", s, s), ("stop", 0, 0),
        ("backward", -s, -s), ("stop", 0, 0),
        ("rotate left", -s, s), ("stop", 0, 0),
        ("rotate right", s, -s), ("stop", 0, 0),
    ]
    for label, left, right in steps:
        log(f"== {label}")
        await hold(ws, left, right, args.seconds)
    await ws.send(json.dumps({"type": "stop"}))
    await asyncio.sleep(0.5)
    task.cancel()
    await ws.close()
    log("bench sequence done")


async def cmd_watchdog(args):
    ws = await open_controller(args)
    task = asyncio.create_task(printer(ws))
    await hold(ws, args.speed, args.speed, 1.5)
    log("!! going silent (socket stays open) - motors must stop within ~500 ms")
    await asyncio.sleep(3)
    task.cancel()
    await ws.close()


async def cmd_garbage(args):
    ws = await open_controller(args)
    task = asyncio.create_task(printer(ws, quiet_telemetry=True))
    bad = [
        "not json",
        "{}",
        '{"type":"move","left":"100","right":100}',
        '{"type":"move","left":100.5,"right":100}',
        '{"type":"move","left":100}',
        '{"type":"warp"}',
    ]
    for b in bad:
        log(f">> {b}")
        await ws.send(b)
        await asyncio.sleep(0.3)
    task.cancel()
    await ws.close()
    log("done: motors must not have moved")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=os.environ.get("ESP32_HOST", "robert-motor.local"))
    p.add_argument("--port", type=int, default=int(os.environ.get("ESP32_PORT", 81)))
    p.add_argument("--token", default=os.environ.get("ESP32_TOKEN", ""))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("monitor")
    d = sub.add_parser("drive")
    d.add_argument("left", type=int)
    d.add_argument("right", type=int)
    d.add_argument("--seconds", type=float, default=2)
    b = sub.add_parser("bench")
    b.add_argument("--speed", type=int, default=80)
    b.add_argument("--seconds", type=float, default=2)
    w = sub.add_parser("watchdog")
    w.add_argument("--speed", type=int, default=80)
    sub.add_parser("garbage")
    args = p.parse_args()

    handlers = {
        "monitor": cmd_monitor, "drive": cmd_drive, "bench": cmd_bench,
        "watchdog": cmd_watchdog, "garbage": cmd_garbage,
    }
    try:
        asyncio.run(handlers[args.cmd](args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
