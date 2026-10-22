"""PWA WebSocket: receives drive/stop, pushes robot state.

Client -> Pi:  {"type":"drive","left":int,"right":int}  (re-send every ~100 ms)
               {"type":"stop"}
Pi -> Client:  {"type":"state", ...RobotState.snapshot(), "max_speed": int}

A driver that disconnects stops the robot (first safety layer; the ESP32
watchdog is the second).
"""

import asyncio
import itertools
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger("robert.api")
router = APIRouter()
_ids = itertools.count(1)


def _as_int(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


@router.websocket("/ws")
async def control_socket(ws: WebSocket) -> None:
    app = ws.app
    motion = app.state.motion
    client_id = f"pwa-{next(_ids)}"
    await ws.accept()
    log.info("%s connected from %s", client_id, ws.client.host if ws.client else "?")

    async def push_state() -> None:
        period = 1 / app.state.settings.state_push_hz
        while True:
            snap = app.state.robot.snapshot()
            snap["type"] = "state"
            snap["client_id"] = client_id
            snap["max_speed"] = motion.max_speed
            await ws.send_text(json.dumps(snap))
            await asyncio.sleep(period)

    pusher = asyncio.create_task(push_state())
    try:
        while True:
            try:
                msg = json.loads(await ws.receive_text())
            except ValueError:
                motion.release(client_id)
                continue
            kind = msg.get("type") if isinstance(msg, dict) else None
            if kind == "stop":
                motion.stop(f"{client_id}_stop")
            elif kind == "drive":
                left, right = _as_int(msg.get("left")), _as_int(msg.get("right"))
                if left is None or right is None:
                    motion.release(client_id)  # malformed drive -> fail toward stop
                    continue
                motion.drive(left, right, client_id)
    except WebSocketDisconnect:
        pass
    finally:
        pusher.cancel()
        motion.release(client_id)
        log.info("%s disconnected", client_id)
