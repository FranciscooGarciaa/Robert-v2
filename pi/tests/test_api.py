from fastapi.testclient import TestClient

from robert.config import Settings
from robert.main import create_app


def make_client():
    app = create_app(Settings(), start_esp32=False, start_lidar=False)
    return TestClient(app), app


def test_drive_then_disconnect_stops():
    client, app = make_client()
    with client:
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "state"
            ws.send_json({"type": "drive", "left": 100, "right": 100})
            ws.send_json({"type": "drive", "left": 100, "right": 100})
            ws.receive_json()
            assert app.state.robot.motion.target_left == 100
        # socket closed -> driver released
        client.get("/api/health")
        assert app.state.robot.motion.target_left == 0
        assert app.state.robot.motion.last_stop_reason == "driver_disconnected"


def test_malformed_drive_stops():
    client, app = make_client()
    with client, client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "drive", "left": 100, "right": 100})
        ws.send_json({"type": "drive", "left": "fast", "right": 100})
        ws.receive_json()
        assert app.state.robot.motion.target_left == 0


def test_http_stop_and_state():
    client, app = make_client()
    with client:
        app.state.motion.drive(50, 50, "x")
        assert client.post("/api/stop").json() == {"ok": True}
        state = client.get("/api/state").json()
        assert state["motion"]["target_left"] == 0
        assert state["esp32"]["connected"] is False


def test_frontend_served():
    client, _ = make_client()
    with client:
        r = client.get("/")
        assert r.status_code == 200
        assert "Robert" in r.text


def test_auto_arm_over_websocket_and_disarm_on_disconnect():
    client, app = make_client()
    with client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "auto", "mode": "explore"})
            ws.send_json({"type": "auto", "mode": "warp"})  # unknown modes are ignored
            ws.receive_json()
        client.get("/api/health")
        assert not app.state.autonomy.active
