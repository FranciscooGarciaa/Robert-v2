import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from fastapi.responses import FileResponse

from ..robot.dance import ROUTINES
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
    style: str | None = None


class VisionPeople(BaseModel):
    people: list[dict]


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
    # The voice reports a finished turn as "listening" + what was heard and said.
    if event.state == "listening" and (event.heard or event.reply):
        request.app.state.memory.record("talk", heard=event.heard, reply=event.reply)
    return {"ok": True}


@router.post("/voice/command")  # async: the nudge task needs the event loop
async def voice_command(cmd: VoiceCommand, request: Request) -> dict:
    _local_only(request)
    app = request.app.state
    if cmd.action == "stop":
        app.motion.stop("voice_stop")
    elif cmd.action == "dance":
        style = cmd.style or "baile"
        if style not in ROUTINES:
            raise HTTPException(status_code=400, detail="unknown dance")
        if not app.robot.esp32.connected:
            return {"ok": False, "reason": "esp32_disconnected"}
        app.autonomy.disarm()
        app.nudger.cancel()
        app.dancer.start(style)
    elif cmd.action in ("social_on", "social_off"):
        app.robot.nav.social = app.social.enabled = cmd.action == "social_on"
    elif cmd.action == "follow_off":
        app.motion.stop("voice_follow_off")
    elif cmd.action == "follow":
        if not app.robot.esp32.connected:
            return {"ok": False, "reason": "esp32_disconnected"}
        app.nudger.cancel()
        app.autonomy.start_follow("voice")
    elif cmd.action in MOVES:
        if not app.robot.esp32.connected:
            return {"ok": False, "reason": "esp32_disconnected"}
        app.autonomy.disarm()
        app.nudger.start(cmd.action)
    else:
        raise HTTPException(status_code=400, detail="unknown action")
    app.memory.record("command", action=cmd.action, style=cmd.style)
    return {"ok": True}


# --- memory (what Robert has lived through; see robert/memory.py) ------------

class MemoryEvent(BaseModel):
    kind: str
    data: dict = {}


class MemoryNote(BaseModel):
    text: str


class MemoryStory(BaseModel):
    text: str
    when: str | None = None   # "2026-10-25": when it happened, if not today


@router.get("/memory")
def memory_summary(request: Request) -> dict:
    """memory.json: totals, last sessions, notes and recent conversations."""
    return request.app.state.memory.snapshot()


@router.get("/memory/session")
def memory_session(request: Request) -> dict:
    """The diary of the current session (one event per line on disk)."""
    m = request.app.state.memory
    return {"session": m.session_id, "events": m.diary()}


@router.get("/memory/recall")
def memory_recall(request: Request) -> dict:
    """What the voice puts in Robert's prompt."""
    return {"text": request.app.state.memory.recall()}


@router.post("/memory/event")
def memory_event(event: MemoryEvent, request: Request) -> dict:
    _local_only(request)
    if event.kind in ("boot", "shutdown") or not event.kind.isidentifier():
        raise HTTPException(status_code=400, detail="bad kind")
    return {"ok": True, "event": request.app.state.memory.record(event.kind, **event.data)}


@router.post("/memory/note")
def memory_note(note: MemoryNote, request: Request) -> dict:
    _local_only(request)
    event = request.app.state.memory.note(note.text)
    if event is None:
        raise HTTPException(status_code=400, detail="empty note")
    return {"ok": True, "event": event}


@router.post("/memory/story")
def memory_story(story: MemoryStory, request: Request) -> dict:
    """Adds a story in Robert's own voice (what he lived through) to his memory."""
    _local_only(request)
    event = request.app.state.memory.story(story.text, story.when)
    if event is None:
        raise HTTPException(status_code=400, detail="empty story")
    return {"ok": True, "event": event}


@router.post("/vision/people")
def vision_people(body: VisionPeople, request: Request) -> dict:
    _local_only(request)
    v = request.app.state.robot.vision
    v.people = body.people
    v.updated = time.monotonic()
    return {"ok": True}


# --- selfies taken by the vision service --------------------------------------

def _photos_dir(request: Request):
    return request.app.state.settings.photos_dir


@router.get("/photos")
def photos(request: Request) -> dict:
    d = _photos_dir(request)
    files = sorted(d.glob("robert-*.jpg"), reverse=True) if d.is_dir() else []
    return {"photos": [f.name for f in files[:30]]}


@router.get("/photos/{name}")
def photo(name: str, request: Request) -> FileResponse:
    d = _photos_dir(request).resolve()
    path = (d / name).resolve()
    if path.parent != d or not name.startswith("robert-") or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/jpeg", filename=name)
