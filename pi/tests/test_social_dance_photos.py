import asyncio

from fastapi.testclient import TestClient

from navsim import scan_view, wall
from robert.config import Settings
from robert.main import create_app
from robert.nav.social import COOLDOWN_S, HOLD_S, SocialGreeter
from robert.robot import dance


def person_at(x, y, size=0.3):
    h = size / 2
    return (x - h, x + h, y - h, y + h)


def pts(boxes):
    return scan_view(boxes)[0].points()


# --- social mode -----------------------------------------------------------------

def test_social_greets_after_hold_then_waits_for_cooldown():
    g = SocialGreeter()
    g.enabled = True
    near = pts([person_at(0.9, 0.0)])
    assert not g.update(near, busy=False, now=0.0)
    assert not g.update(near, busy=False, now=HOLD_S - 0.1)
    assert g.update(near, busy=False, now=HOLD_S + 0.1)
    assert not g.update(near, busy=False, now=HOLD_S + 5)              # cooldown
    assert not g.update(near, busy=False, now=HOLD_S + COOLDOWN_S - 1)
    # Still standing there once the cooldown is over: greeted again.
    assert g.update(near, busy=False, now=HOLD_S + COOLDOWN_S + 1)


def test_social_ignores_far_moving_disabled_or_walls():
    g = SocialGreeter()
    g.enabled = True
    for t in range(10):
        assert not g.update(pts([person_at(2.5, 0.0)]), busy=False, now=float(t))   # too far
        assert not g.update(pts([person_at(0.9, 0.0)]), busy=True, now=float(t))    # robot busy
        assert not g.update(pts([wall(0.8)]), busy=False, now=float(t))             # a wall is no one
    g.enabled = False
    near = pts([person_at(0.9, 0.0)])
    assert not any(g.update(near, busy=False, now=float(t)) for t in range(10))


# --- dances ------------------------------------------------------------------------

def make(local=True):
    app = create_app(Settings(), start_esp32=False, start_lidar=False)
    return TestClient(app, client=("127.0.0.1" if local else "10.0.0.9", 5555)), app


def test_routines_are_short():
    assert all(dance.duration(n) <= dance.MAX_S for n in dance.ROUTINES)
    assert 3 < dance.duration("vuelta") < 4.5   # ~360 deg at the calibrated spin rate


def test_dance_runs_bounded_and_stop_cancels(monkeypatch):
    monkeypatch.setattr(dance, "MAX_S", 0.4)
    client, app = make()
    with client:
        app.state.robot.esp32.connected = True
        assert client.post("/api/voice/command", json={"action": "dance", "style": "baile"}).json() == {"ok": True}
        m = app.state.robot.motion
        client.get("/api/health")
        assert m.owner == "dance"
        client.portal.call(asyncio.sleep, 0.6)
        assert m.mode == "idle" and m.last_stop_reason == "dance_done"
        client.post("/api/voice/command", json={"action": "dance", "style": "vuelta"})
        client.post("/api/stop")
        assert m.mode == "idle" and not app.state.dancer.active


def test_dance_rejects_unknown_styles_and_other_hosts():
    client, _ = make()
    with client:
        assert client.post("/api/voice/command", json={"action": "dance", "style": "moonwalk"}).status_code == 400
    client, _ = make(local=False)
    with client:
        assert client.post("/api/voice/command", json={"action": "dance"}).status_code == 403


def test_social_toggle_via_voice_command():
    client, app = make()
    with client:
        client.post("/api/voice/command", json={"action": "social_on"})
        assert app.state.social.enabled and client.get("/api/state").json()["nav"]["social"]
        client.post("/api/voice/command", json={"action": "social_off"})
        assert not app.state.social.enabled


# --- photos ------------------------------------------------------------------------

def test_photos_listed_and_served_but_nothing_outside(tmp_path):
    (tmp_path / "robert-20261025-101010.jpg").write_bytes(b"\xff\xd8a")
    (tmp_path / "robert-20261025-111111.jpg").write_bytes(b"\xff\xd8b")
    (tmp_path / "secret.txt").write_text("no")
    app = create_app(Settings(photos_dir=tmp_path), start_esp32=False, start_lidar=False)
    with TestClient(app) as client:
        assert client.get("/api/photos").json() == {"photos": ["robert-20261025-111111.jpg",
                                                              "robert-20261025-101010.jpg"]}
        assert client.get("/api/photos/robert-20261025-101010.jpg").content == b"\xff\xd8a"
        assert client.get("/api/photos/secret.txt").status_code == 404
        assert client.get("/api/photos/..%2Fsecret.txt").status_code == 404
