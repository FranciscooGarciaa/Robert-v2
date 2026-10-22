"""Simulated ESP32 motor controller (protocol v1) for development without hardware.

    python -m robert.sim.fake_esp32 --port 8181 --token dev --distance 80

Mirrors the firmware's safety semantics: token handshake, single controller,
500 ms watchdog, stop on disconnect, forward veto below 15 cm. No ramping.
"""

import argparse
import asyncio
import json
import logging
import time

from websockets.asyncio.server import ServerConnection, serve

log = logging.getLogger("fake_esp32")

WATCHDOG_S = 0.5
STOP_CM = 15.0
MAX_PWM = 255


class FakeEsp32:
    def __init__(self, token: str, distance_cm: float | None = 80.0):
        self.token = token
        self.distance_cm = distance_cm
        self.controller: ServerConnection | None = None
        self.req = (0, 0)
        self.failsafe = "boot"
        self.last_cmd = 0.0
        self.started = time.monotonic()
        self.moves: list[tuple[int, int]] = []  # every accepted move, for tests

    @property
    def obstacle(self) -> bool:
        return self.distance_cm is not None and self.distance_cm < STOP_CM

    @property
    def output(self) -> tuple[int, int]:
        left, right = self.req
        if self.obstacle and left + right > 0:
            return (0, 0)
        return self.req

    def trip(self, reason: str) -> None:
        if self.req != (0, 0) or self.failsafe != reason:
            log.warning("[SAFE] %s -> STOP", reason)
        self.req = (0, 0)
        self.failsafe = reason

    def telemetry(self) -> dict:
        return {
            "type": "telemetry",
            "distance_cm": self.distance_cm,
            "obstacle": self.obstacle,
            "us_timeouts": 0,
            "left_target": self.req[0],
            "right_target": self.req[1],
            "left_out": self.output[0],
            "right_out": self.output[1],
            "failsafe": "obstacle" if self.obstacle and sum(self.req) > 0 else self.failsafe,
            "wifi_rssi": -50,
            "uptime_ms": int((time.monotonic() - self.started) * 1000),
        }

    async def handler(self, ws: ServerConnection) -> None:
        try:
            async for raw in ws:
                await self._on_message(ws, raw)
        finally:
            if ws is self.controller:
                self.controller = None
                self.trip("disconnect")

    async def _on_message(self, ws: ServerConnection, raw) -> None:
        try:
            msg = json.loads(raw)
            kind = msg["type"]
        except (ValueError, KeyError, TypeError):
            await ws.send(json.dumps({"type": "error", "error": "bad_json"}))
            if ws is self.controller:
                self.trip("malformed")
            return

        if kind == "hello":
            if msg.get("token") != self.token or msg.get("v") != 1:
                await ws.send(json.dumps({"type": "error", "error": "bad_token"}))
                await ws.close()
                return
            if self.controller is not None and self.controller is not ws:
                old, self.controller = self.controller, ws
                self.trip("handover")
                await old.close()
            self.controller = ws
            log.info("[CTRL] controller connected")
            await ws.send(json.dumps({"type": "hello_ack", "v": 1, "watchdog_ms": 500,
                                      "max_pwm": MAX_PWM, "reset_reason": "poweron"}))
            return

        if ws is not self.controller:
            await ws.send(json.dumps({"type": "error", "error": "not_controller"}))
            return

        now = time.monotonic()
        if kind == "move":
            left, right = msg.get("left"), msg.get("right")
            if not all(isinstance(v, int) and not isinstance(v, bool) for v in (left, right)):
                await ws.send(json.dumps({"type": "error", "error": "bad_speed"}))
                self.trip("malformed")
                return
            req = (max(-MAX_PWM, min(MAX_PWM, left)), max(-MAX_PWM, min(MAX_PWM, right)))
            if req != self.req:
                log.info("[MOVE] L=%d R=%d", *req)
            self.req = req
            self.moves.append(req)
            self.failsafe = "none"
            self.last_cmd = now
        elif kind == "stop":
            self.req = (0, 0)
            self.last_cmd = now
        elif kind == "heartbeat":
            self.last_cmd = now
        else:
            await ws.send(json.dumps({"type": "error", "error": "unknown_type"}))
            self.trip("malformed")

    async def tick(self) -> None:
        """Watchdog + 5 Hz telemetry."""
        while True:
            if self.req != (0, 0) and time.monotonic() - self.last_cmd > WATCHDOG_S:
                self.trip("watchdog")
            if self.controller is not None:
                try:
                    await self.controller.send(json.dumps(self.telemetry()))
                except Exception:  # noqa: BLE001 - handler() cleans up
                    pass
            await asyncio.sleep(0.2)

    async def serve(self, host: str, port: int):
        """Start serving; returns the websockets Server (caller closes it)."""
        server = await serve(self.handler, host, port)
        self._tick_task = asyncio.create_task(self.tick())
        return server


async def _main(args) -> None:
    fake = FakeEsp32(args.token, args.distance)
    server = await fake.serve(args.host, args.port)
    log.info("fake ESP32 on ws://%s:%d/", args.host, args.port)
    await server.serve_forever()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8181)
    p.add_argument("--token", default="dev")
    p.add_argument("--distance", type=float, default=80.0)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    try:
        asyncio.run(_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
