import asyncio
from types import SimpleNamespace as NS

import numpy as np

from robert.voice.live import LiveConversation, describe_state


class FakeSession:
    """Plays a scripted list of turns; each turn is a list of server messages."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.sent_audio = 0
        self.sent_video = []
        self.tool_responses = []

    async def send_realtime_input(self, audio=None, video=None):
        if audio is not None:
            self.sent_audio += 1
        if video is not None:
            self.sent_video.append(video)

    async def send_tool_response(self, function_responses):
        self.tool_responses.extend(function_responses)

    async def receive(self):
        if not self.turns:
            await asyncio.sleep(3600)  # nothing more from the server
        for msg in self.turns.pop(0):
            await asyncio.sleep(0)
            yield msg


class FakeClient:
    def __init__(self, session):
        session_ = session

        class Live:
            def connect(self, model, config):
                class Ctx:
                    async def __aenter__(self_inner):
                        return session_

                    async def __aexit__(self_inner, *exc):
                        return False
                return Ctx()
        self.aio = NS(live=Live())


class FakeMic:
    def __init__(self):
        self.muted = False
        self.mutes = []

    def read(self, timeout=None):
        import time
        time.sleep(0.02)
        return None if self.muted else np.zeros(1280, np.int16)

    def mute(self, on):
        self.muted = on
        self.mutes.append(on)


class FakePlayer:
    def __init__(self):
        self.written = []

    def write(self, pcm):
        self.written.append(pcm)

    busy = False

    def remaining_s(self):
        return 0.0


class FakeBackend:
    def __init__(self):
        self.commands, self.reports, self.memory = [], [], []

    def command(self, action, style=None):
        self.commands.append(action)
        return {"ok": True}

    def state(self):
        return {"esp32": {"connected": True}, "nav": {"clearance": {"front": 1.24}}, "motion": {"mode": "idle"}}

    def report(self, state, heard=None, reply=None):
        self.reports.append((state, heard, reply))

    def recall(self):
        return "Tu memoria: te han encendido 3 veces."

    def remember(self, kind, **data):
        self.memory.append((kind, data))

    def note(self, text):
        self.memory.append(("note", text))
        return {"ok": True}


def content(**kw):
    base = dict(input_transcription=None, output_transcription=None, model_turn=None, turn_complete=False)
    base.update(kw)
    return NS(go_away=None, tool_call=None, server_content=NS(**base))


def audio(pcm):
    return content(model_turn=NS(parts=[NS(inline_data=NS(data=pcm))]))


def tool(name, **args):
    return NS(go_away=None, server_content=None,
              tool_call=NS(function_calls=[NS(id="1", name=name, args=args)]))


def converse(turns, idle_s=0.4, camera=lambda: None):
    session, mic, player, backend = FakeSession(turns), FakeMic(), FakePlayer(), FakeBackend()
    talk = LiveConversation(FakeClient(session), "m", None, mic, player,
                            NS(is_speech=lambda f: False), backend, idle_s, camera)
    asyncio.run(asyncio.wait_for(talk.run(), 5))
    return session, mic, player, backend


def test_reply_audio_streams_and_mic_is_muted_meanwhile():
    turns = [[content(input_transcription=NS(text="hola robert")), audio(b"ab"), audio(b"cd"),
              content(output_transcription=NS(text="¡Hola!")), content(turn_complete=True)]]
    session, mic, player, backend = converse(turns)
    assert player.written == [b"ab", b"cd"]
    assert session.sent_audio > 0
    assert mic.mutes[:2] == [True, False] and mic.muted is False
    assert ("speaking", "hola robert", None) in backend.reports
    assert ("listening", "hola robert", "¡Hola!") in backend.reports


def test_tools_reach_the_backend_and_get_answered():
    turns = [[tool("parar")], [tool("mover", direccion="adelante")], [tool("mover", direccion="arriba")],
             [tool("estado_robot")]]
    session, _, _, backend = converse(turns)
    assert backend.commands == ["stop", "forward"]
    results = [r.response for r in session.tool_responses]
    assert results[2]["ok"] is False
    assert "delante: 1.2 m" in results[3]["estado"]


def test_goodbye_ends_the_conversation_early():
    session, _, _, _ = converse([[tool("despedirse")]], idle_s=60)
    assert session.tool_responses[0].response == {"ok": True}


def test_describe_state_never_uses_raw_lidar_distance():
    text = describe_state({"esp32": {"connected": True}, "lidar": {"nearest_mm": 74},
                           "nav": {"clearance": {}}, "motion": {"mode": "idle"}})
    assert "0.1" not in text and "sin datos del LiDAR" in text
    assert describe_state(None) == "Estado del robot: desconocido."


def test_wake_phrase_and_prompt():
    from robert.voice.live import SYSTEM_PROMPT, wake_phrase

    assert wake_phrase("hey_jarvis") == "Hey Jarvis"
    assert wake_phrase("/home/x/robert-models/robert.onnx") == "Robert"
    assert '"Robert"' in SYSTEM_PROMPT.format(wake="Robert")


def test_look_answers_with_the_photo_attached():
    session, _, _, _ = converse([[tool("mirar")]], camera=lambda: b"\xff\xd8jpeg")
    reply = session.tool_responses[0]
    assert reply.parts[0].inline_data.mime_type == "image/jpeg"
    assert reply.parts[0].inline_data.data == b"\xff\xd8jpeg"


def test_look_without_camera_says_so():
    session, _, _, _ = converse([[tool("mirar")]])
    assert session.sent_video == []
    assert session.tool_responses[0].response == {"ok": False, "reason": "la cámara no responde"}


def test_follow_tool_reports_when_it_found_someone():
    class FollowBackend(FakeBackend):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def state(self):
            self.calls += 1
            st = "following" if self.calls > 1 else "searching"
            return {"nav": {"follow": {"state": st, "distance_m": 1.8}}}

    session, mic, player = FakeSession([[tool("seguirme")]]), FakeMic(), FakePlayer()
    backend = FollowBackend()
    talk = LiveConversation(FakeClient(session), "m", None, mic, player,
                            NS(is_speech=lambda f: False), backend, 2.0)
    asyncio.run(asyncio.wait_for(talk.run(), 10))
    assert backend.commands == ["follow"]
    assert session.tool_responses[0].response == {"ok": True, "estado": "te sigo; estás a 1.8 m"}


def test_fun_tools_reach_the_backend():
    class FunBackend(FakeBackend):
        def command(self, action, style=None):
            self.commands.append((action, style))
            return {"ok": True}

    session, mic, player = FakeSession([[tool("bailar", estilo="vuelta")], [tool("modo_social", activar=True)],
                                        [tool("hacer_foto")]]), FakeMic(), FakePlayer()
    backend = FunBackend()
    talk = LiveConversation(FakeClient(session), "m", None, mic, player, NS(is_speech=lambda f: False),
                            backend, 0.5, full_photo=lambda: "robert-1.jpg")
    asyncio.run(asyncio.wait_for(talk.run(), 10))
    assert backend.commands == [("dance", "vuelta"), ("social_on", None)]
    assert session.tool_responses[2].response["foto"] == "robert-1.jpg"
    assert len(player.written) >= 3            # chirps, countdown, shutter


def test_opening_line_makes_robert_speak_first():
    class OpeningSession(FakeSession):
        def __init__(self, turns):
            super().__init__(turns)
            self.client_content = []

        async def send_client_content(self, turns, turn_complete):
            self.client_content.append(turns.parts[0].text)

    session = OpeningSession([])
    talk = LiveConversation(FakeClient(session), "m", None, FakeMic(), FakePlayer(), NS(is_speech=lambda f: False),
                            FakeBackend(), 0.3, opening="Saluda")
    asyncio.run(asyncio.wait_for(talk.run(), 5))
    assert session.client_content == ["Saluda"]


def test_memory_tools_read_and_write_through_the_backend():
    session, _, _, backend = converse([[tool("recordar")], [tool("apuntar", nota="Fran vive en el tercero")],
                                       [tool("apuntar", nota="  ")]])
    assert "encendido 3 veces" in session.tool_responses[0].response["memoria"]
    assert backend.memory == [("note", "Fran vive en el tercero")]
    assert session.tool_responses[2].response["ok"] is False


def test_prompt_carries_the_memory(monkeypatch):
    import sys
    from robert.voice.live import session_config

    captured = {}

    class Stub:  # stands in for google.genai.types: anything callable, any attribute
        def __init__(self, name=""):
            self.name = name

        def __getattr__(self, name):
            return Stub(name)

        def __call__(self, **kw):
            if self.name == "LiveConnectConfig":
                captured.update(kw)
            return NS(**kw)

    monkeypatch.setitem(sys.modules, "google", NS(genai=NS(types=Stub())))
    monkeypatch.setitem(sys.modules, "google.genai", NS(types=Stub()))
    session_config("Puck", 600, None, "Robert", memory="Tu memoria: dos fotos.")
    assert captured["system_instruction"].endswith("Tu memoria: dos fotos.")
    names = [f.name for f in captured["tools"][0].function_declarations]
    assert "recordar" in names and "apuntar" in names
