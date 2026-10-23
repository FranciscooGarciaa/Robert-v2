import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..robot.nudge import MOVES

router = APIRouter(prefix="/api")


@router.get("/state")
def get_state(request: Request) -> dict:
    return request.app.state.robot.snapshot()


@router.post("/stop")
def post_stop(request: Request) -> dict:
    request.app.state.motion.stop("http_stop")
    return {"ok": True}


@router.get("/health")
def health(request: Request) -> dict:
    return {"ok": True, "esp32_connected": request.app.state.robot.esp32.connected}


@router.get("/lidar/scan")
def lidar_scan(request: Request) -> dict:
    lidar = request.app.state.robot.lidar
    return {"connected": lidar.connected, "bins": lidar.bins}


# --- voice service (same machine only) --------------------------------------
# The voice process reports what it heard/said and forwards spoken commands.
# Nothing off the Pi may call these: they bypass the PWA's dead-man.

LOCAL_HOSTS = {"127.0.0.1", "::1"}


def _local_only(request: Request) -> None:
    if not request.client or request.client.host not in LOCAL_HOSTS:
        raise HTTPException(status_code=403, detail="local only")


class VoiceEvent(BaseModel):
    state: str
    heard: str | None = None
    reply: str | None = None


class VoiceCommand(BaseModel):
    action: str


@router.post("/voice/event")
def voice_event(event: VoiceEvent, request: Request) -> dict:
    _local_only(request)
    v = request.app.state.robot.voice
    v.state = event.state
    if event.heard is not None:
        v.heard = event.heard
    if event.reply is not None:
        v.reply = event.reply
    v.updated = time.monotonic()
    return {"ok": True}


@router.post("/voice/command")  # async: the nudge task needs the event loop
async def voice_command(cmd: VoiceCommand, request: Request) -> dict:
    _local_only(request)
    app = request.app.state
    if cmd.action == "stop":
        app.motion.stop("voice_stop")
    elif cmd.action in MOVES:
        if not app.robot.esp32.connected:
            return {"ok": False, "reason": "esp32_disconnected"}
        app.autonomy.disarm()
        app.nudger.start(cmd.action)
    else:
        raise HTTPException(status_code=400, detail="unknown action")
    return {"ok": True}
