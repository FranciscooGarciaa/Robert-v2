"""WebSocket link to the ESP32 motor controller.

Keeps one connection alive (reconnecting with backoff), streams the current
motion target at `esp32_send_hz` (which doubles as the ESP32 heartbeat), and
copies telemetry into the shared state. The ESP32 watchdog stops the motors
on its own if this loop ever stalls or the link drops.
"""

import asyncio
import json
import logging
import time

from websockets.asyncio.client import ClientConnection, connect

from ..config import Settings
from .motion import MotionController
from .state import RobotState

log = logging.getLogger("robert.esp32")

PROTOCOL_VERSION = 1


class Esp32Client:
    def __init__(self, settings: Settings, state: RobotState, motion: MotionController):
        self._s = settings
        self._state = state
        self._motion = motion
        self._wake = asyncio.Event()
        self._stop_pending = False
        motion.on_stop(self._request_stop)

    @property
    def uri(self) -> str:
        return f"ws://{self._s.esp32_host}:{self._s.esp32_port}/"

    def _request_stop(self) -> None:
        # Called synchronously by MotionController; flush on the next tick.
        self._stop_pending = True
        self._wake.set()

    async def run(self) -> None:
        backoff = 0.5
        while True:
            try:
                async with connect(
                    self.uri, open_timeout=3, ping_interval=1, ping_timeout=2, close_timeout=1
                ) as ws:
                    await self._handshake(ws)
                    backoff = 0.5
                    await self._session(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure means reconnect
                while isinstance(exc, ExceptionGroup) and exc.exceptions:
                    exc = exc.exceptions[0]
                if self._state.esp32.connected:
                    log.warning("ESP32 link lost: %r", exc)
                else:
                    log.debug("ESP32 connect failed: %r", exc)
            finally:
                if self._state.esp32.connected:
                    self._state.esp32.connected = False
                    # Nobody can be driving a robot we cannot reach.
                    self._motion.stop("esp32_disconnected")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 5.0)

    async def _handshake(self, ws: ClientConnection) -> None:
        await ws.send(json.dumps({"type": "hello", "v": PROTOCOL_VERSION, "token": self._s.esp32_token}))
        ack = json.loads(await asyncio.wait_for(ws.recv(), 3))
        if ack.get("type") != "hello_ack":
            raise ConnectionError(f"handshake rejected: {ack}")
        e = self._state.esp32
        e.connected = True
        e.reconnects += 1
        e.reset_reason = ack.get("reset_reason")
        e.last_telemetry = time.monotonic()
        log.info("ESP32 connected (%s), reset_reason=%s", self.uri, e.reset_reason)
        if e.reset_reason not in (None, "poweron", "software"):
            log.warning("ESP32 reports unexpected reset: %s", e.reset_reason)

    async def _session(self, ws: ClientConnection) -> None:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._sender(ws))
            tg.create_task(self._receiver(ws))

    async def _sender(self, ws: ClientConnection) -> None:
        period = 1 / self._s.esp32_send_hz
        while True:
            if self._stop_pending:
                self._stop_pending = False
                await ws.send('{"type":"stop"}')
            left, right = self._motion.current_target()
            await ws.send(json.dumps({"type": "move", "left": left, "right": right}))
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), period)
            except TimeoutError:
                pass

    async def _receiver(self, ws: ClientConnection) -> None:
        while True:
            raw = await asyncio.wait_for(ws.recv(), self._s.esp32_telemetry_timeout_s)
            try:
                msg = json.loads(raw)
            except ValueError:
                log.warning("ESP32 sent invalid JSON: %r", raw[:100])
                continue
            kind = msg.get("type")
            if kind == "telemetry":
                self._apply_telemetry(msg)
            elif kind == "event":
                level = logging.INFO if msg.get("failsafe") == "none" else logging.WARNING
                log.log(level, "ESP32 safety event: failsafe=%s distance=%s",
                        msg.get("failsafe"), msg.get("distance_cm"))
            elif kind == "error":
                log.error("ESP32 rejected a message: %s", msg.get("error"))

    def _apply_telemetry(self, msg: dict) -> None:
        e = self._state.esp32
        e.last_telemetry = time.monotonic()
        e.failsafe = msg.get("failsafe")
        e.wifi_rssi = msg.get("wifi_rssi")
        e.uptime_ms = msg.get("uptime_ms")
        e.left_target = msg.get("left_target", 0)
        e.right_target = msg.get("right_target", 0)
        e.left_out = msg.get("left_out", 0)
        e.right_out = msg.get("right_out", 0)
        u = self._state.ultrasonic
        u.distance_cm = msg.get("distance_cm")
        u.obstacle = bool(msg.get("obstacle"))
        u.timeouts = msg.get("us_timeouts", 0)
