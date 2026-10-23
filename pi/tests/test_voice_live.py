import asyncio
from types import SimpleNamespace as NS

import numpy as np

from robert.voice.live import LiveConversation, describe_state


class FakeSession:
    """Plays a scripted list of turns; each turn is a list of server messages."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.sent_audio = 0
        self.tool_responses = []

    async def send_realtime_input(self, audio):
        self.sent_audio += 1

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
        self.commands, self.reports = [], []

    def command(self, action):
        self.commands.append(action)
        return {"ok": True}

    def state(self):
        return {"esp32": {"connected": True}, "nav": {"clearance": {"front": 1.24}}, "motion": {"mode": "idle"}}

    def report(self, state, heard=None, reply=None):
        self.reports.append((state, heard, reply))


def content(**kw):
    base = dict(input_transcription=None, output_transcription=None, model_turn=None, turn_complete=False)
    base.update(kw)
    return NS(go_away=None, tool_call=None, server_content=NS(**base))


def audio(pcm):
    return content(model_turn=NS(parts=[NS(inline_data=NS(data=pcm))]))


def tool(name, **args):
    return NS(go_away=None, server_content=None,
              tool_call=NS(function_calls=[NS(id="1", name=name, args=args)]))


def converse(turns, idle_s=0.4):
    session, mic, player, backend = FakeSession(turns), FakeMic(), FakePlayer(), FakeBackend()
    talk = LiveConversation(FakeClient(session), "m", None, mic, player,
                            NS(is_speech=lambda f: False), backend, idle_s)
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
