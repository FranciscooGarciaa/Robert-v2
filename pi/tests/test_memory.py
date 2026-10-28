import json
import os

import pytest
from fastapi.testclient import TestClient

from robert.config import Settings
from robert.main import create_app
from robert.memory import KEEP_SESSIONS, Memory
from robert.robot.state import RobotState


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_diary_and_summary_carry_over_between_sessions(tmp_path):
    clock = Clock()
    m = Memory(tmp_path, clock=clock)
    m.record("talk", heard="hola", reply="¡Hola!")
    m.note("Fran vive en el tercero")
    m.record("photo", file="robert-1.jpg")
    clock.t += 125
    m.close()

    lines = [json.loads(l) for l in m.session_path.read_text().splitlines()]
    assert [e["kind"] for e in lines] == ["boot", "talk", "note", "photo", "shutdown"]
    assert lines[1]["heard"] == "hola" and lines[-1]["uptime_s"] == 125

    clock.t += 3600
    m2 = Memory(tmp_path, clock=clock)           # next boot: reads memory.json
    s = m2.snapshot()
    assert s["boots"] == 2 and s["totals"] == {"talk": 1, "note": 1, "photo": 1}
    assert s["notes"][0]["text"] == "Fran vive en el tercero"
    assert [x["duration_s"] for x in s["sessions"]] == [125, None]
    text = m2.recall()
    assert "encendido 2 veces" in text and "1 foto" in text and "fotos" not in text
    assert s["first_boot"] == "2027-01-15T" + s["first_boot"][11:]  # set once, on the first boot
    assert "duró 2 min" in text and "Fran vive en el tercero" in text
    assert "te dijeron «hola» y contestaste «¡Hola!»" in text


def test_observe_records_transitions_only(tmp_path):
    m = Memory(tmp_path, clock=Clock())
    robot = RobotState()
    m.observe(robot)
    m.observe(robot)                              # nothing changed: nothing recorded
    robot.esp32.connected = True
    robot.esp32.reset_reason = "poweron"
    m.observe(robot)
    robot.voice.state = "listening"
    m.observe(robot)
    robot.nav.follow = {"state": "searching"}
    m.observe(robot)
    robot.nav.follow = {"state": "following", "distance_m": 0.7}
    m.observe(robot)
    m.observe(robot)
    robot.nav.follow = {"state": "stopped", "reason": "follow_lost"}
    robot.esp32.failsafe = "ultrasonic"
    robot.ultrasonic.distance_cm = 12.0
    m.observe(robot)
    robot.voice.state = "idle"
    robot.esp32.connected = False
    robot.nav.dancing = True
    m.observe(robot)
    kinds = [e["kind"] for e in m.diary()]
    assert kinds == ["boot", "esp32_connected", "conversation", "follow", "failsafe", "follow_end",
                     "esp32_lost", "conversation_end", "dance"]
    events = {e["kind"]: e for e in m.diary()}
    assert events["follow_end"]["reason"] == "follow_lost"
    assert events["failsafe"] == {**events["failsafe"], "failsafe": "ultrasonic", "distance_cm": 12.0}
    assert m.snapshot()["totals"] == {"conversation": 1, "follow": 1, "failsafe": 1, "esp32_lost": 1,
                                      "dance": 1}


def test_old_diaries_are_pruned_and_bad_summary_is_ignored(tmp_path):
    (tmp_path / "sessions").mkdir()
    for i in range(KEEP_SESSIONS + 5):
        (tmp_path / "sessions" / f"2026010{i:02d}-000000.jsonl").write_text("{}\n")
    (tmp_path / "memory.json").write_text("not json")
    m = Memory(tmp_path, clock=Clock())
    assert len(list((tmp_path / "sessions").glob("*.jsonl"))) == KEEP_SESSIONS
    assert m.snapshot()["boots"] == 1


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_unwritable_memory_keeps_working_in_ram(tmp_path):
    root = tmp_path / "ro"
    root.mkdir()
    root.chmod(0o500)
    try:
        m = Memory(root / "memory", clock=Clock())
        m.note("se guarda en RAM")
        assert "se guarda en RAM" in m.recall()
        assert m.diary() == []
    finally:
        root.chmod(0o700)


def test_seed_stories_are_added_once_and_recalled(tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"stories": [{"when": "2026-10-25", "text": "Salí a la calle con Fran."}]}))
    m = Memory(tmp_path / "mem", clock=Clock(), seed=seed)
    m.story("Hoy he bailado en el salón.")
    assert "- (2026-10-25) Salí a la calle con Fran." in m.recall()
    assert "- (2027-01-15) Hoy he bailado en el salón." in m.recall()
    m2 = Memory(tmp_path / "mem", clock=Clock(), seed=seed)   # next boot: not added again
    assert [x["text"] for x in m2.snapshot()["stories"]] == ["Salí a la calle con Fran.",
                                                            "Hoy he bailado en el salón."]
    Memory(tmp_path / "mem", clock=Clock(), seed=tmp_path / "missing.json")   # no seed: fine


def test_disabled_memory_writes_nothing(tmp_path):
    m = Memory(tmp_path, enabled=False, clock=Clock())
    m.record("photo")
    assert not (tmp_path / "memory.json").exists() and m.snapshot()["totals"] == {"photo": 1}


def make(tmp_path, local=True):
    app = create_app(Settings(memory_dir=tmp_path, memory_seed=tmp_path / "none.json"),
                     start_esp32=False, start_lidar=False)
    return TestClient(app, client=("127.0.0.1" if local else "10.0.0.9", 5555)), app


def test_api_records_voice_turns_commands_and_notes(tmp_path):
    client, app = make(tmp_path)
    with client:
        client.post("/api/voice/event", json={"state": "speaking", "heard": "¿qué ves?"})   # mid-turn
        client.post("/api/voice/event", json={"state": "listening", "heard": "¿qué ves?", "reply": "Una mesa."})
        client.post("/api/voice/event", json={"state": "listening"})                        # heartbeat
        client.post("/api/voice/command", json={"action": "social_on"})
        r = client.post("/api/memory/note", json={"text": "Marta es la vecina"})
        assert r.json()["event"]["kind"] == "note"
        r = client.post("/api/memory/story", json={"text": "Perdí dos ruedas.", "when": "2026-10-25"})
        assert r.json()["event"]["when"] == "2026-10-25"
        assert client.post("/api/memory/note", json={"text": " "}).status_code == 400
        r = client.post("/api/memory/event", json={"kind": "photo", "data": {"file": "robert-1.jpg"}})
        assert r.json()["event"]["file"] == "robert-1.jpg"
        assert client.post("/api/memory/event", json={"kind": "boot"}).status_code == 400

        kinds = [e["kind"] for e in client.get("/api/memory/session").json()["events"]]
        assert kinds == ["boot", "talk", "command", "note", "story", "photo"]
        summary = client.get("/api/memory").json()
        assert summary["recent_talks"] == [{**summary["recent_talks"][0], "heard": "¿qué ves?", "reply": "Una mesa."}]
        assert "Marta es la vecina" in client.get("/api/memory/recall").json()["text"]


def test_memory_writes_are_local_only(tmp_path):
    client, _ = make(tmp_path, local=False)
    with client:
        assert client.post("/api/memory/note", json={"text": "x"}).status_code == 403
        assert client.post("/api/memory/event", json={"kind": "photo"}).status_code == 403
        assert client.get("/api/memory").status_code == 200
