import asyncio

from fastapi.testclient import TestClient

from robert.config import Settings
from robert.main import create_app
from robert.robot import nudge


def make(local: bool = True):
    app = create_app(Settings(), start_esp32=False, start_lidar=False)
    client = ("127.0.0.1", 5555) if local else ("192.168.1.50", 5555)
    return TestClient(app, client=client), app


def test_voice_endpoints_reject_other_hosts():
    client, _ = make(local=False)
    with client:
        assert client.post("/api/voice/command", json={"action": "stop"}).status_code == 403
        assert client.post("/api/voice/event", json={"state": "idle"}).status_code == 403


def test_voice_event_shows_in_state_and_expires():
    client, app = make()
    with client:
        client.post("/api/voice/event", json={"state": "speaking", "heard": "hola", "reply": "¡Hola!"})
        v = client.get("/api/state").json()["voice"]
        assert v == {"state": "speaking", "heard": "hola", "reply": "¡Hola!"}
        app.state.robot.voice.updated -= 20  # no report for 20 s
        assert client.get("/api/state").json()["voice"]["state"] == "offline"


def test_unknown_or_unconnected_commands_do_not_move():
    client, app = make()
    with client:
        assert client.post("/api/voice/command", json={"action": "explore"}).status_code == 400
        r = client.post("/api/voice/command", json={"action": "forward"}).json()
        assert r == {"ok": False, "reason": "esp32_disconnected"}
        assert app.state.robot.motion.mode == "idle"


def test_nudge_is_bounded_and_cancelled_by_stop(monkeypatch):
    monkeypatch.setattr(nudge, "NUDGE_S", 0.3)
    client, app = make()
    with client:
        app.state.robot.esp32.connected = True
        assert client.post("/api/voice/command", json={"action": "forward"}).json() == {"ok": True}
        m = app.state.robot.motion
        client.get("/api/health")
        assert m.owner == "voice" and m.target_left > 0
        # Runs out on its own...
        client.portal.call(asyncio.sleep, 0.5)
        assert m.mode == "idle" and m.last_stop_reason == "voice_nudge_done"
        # ...and a stop cancels it straight away.
        client.post("/api/voice/command", json={"action": "left"})
        client.post("/api/voice/command", json={"action": "stop"})
        assert m.mode == "idle" and m.last_stop_reason == "voice_stop"
        client.portal.call(asyncio.sleep, 0.3)
        assert m.mode == "idle"
