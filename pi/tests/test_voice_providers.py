import asyncio
import base64
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS

import pytest

from robert.voice import providers
from robert.voice.config import VoiceSettings
from robert.voice.live import TOOLS, LiveConversation
from robert.voice.providers.openai import OpenAI, session_config

from test_voice_live import FakeBackend, FakeMic, FakePlayer


class FakeWS:
    """The OpenAI Realtime socket: records what is sent, replays scripted server events."""

    def __init__(self, script):
        self.sent, self.script = [], list(script)

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    def kinds(self):
        return [e["type"] for e in self.sent]

    def __aiter__(self):
        return self._events()

    async def _events(self):
        for event in self.script:
            await asyncio.sleep(0)
            yield json.dumps(event)
        await asyncio.sleep(3600)  # the socket stays open


def openai(ws):
    seen = {}

    @asynccontextmanager
    async def dial(url, **kw):
        seen.update(url=url, **kw)
        yield ws
    return OpenAI("sk-test", "gpt-realtime", "marin", 600, dial=dial), seen


def talk_openai(script, idle_s=0.4, camera=lambda: None):
    ws = FakeWS(script)
    provider, seen = openai(ws)
    mic, player, backend = FakeMic(), FakePlayer(), FakeBackend()
    talk = LiveConversation(provider, "Eres Robert.", mic, player, NS(is_speech=lambda f: False),
                            backend, idle_s, camera)
    asyncio.run(asyncio.wait_for(talk.run(), 5))
    return ws, seen, player, backend


def done(*calls):
    return {"type": "response.done", "response": {"status": "completed", "output": [
        {"type": "function_call", "call_id": f"c{i}", "name": name, "arguments": json.dumps(args)}
        for i, (name, args) in enumerate(calls)]}}


def test_openai_session_is_configured_with_key_prompt_voice_and_tools():
    ws, seen, _, _ = talk_openai([])
    assert seen["url"].endswith("model=gpt-realtime")
    assert seen["additional_headers"]["Authorization"] == "Bearer sk-test"
    first = ws.sent[0]
    assert first["type"] == "session.update"
    session = first["session"]
    assert session["instructions"] == "Eres Robert." and session["audio"]["output"]["voice"] == "marin"
    assert session["audio"]["input"]["turn_detection"]["silence_duration_ms"] == 600
    assert {t["name"] for t in session["tools"]} == {t["name"] for t in TOOLS}
    assert all(t["parameters"]["type"] == "object" for t in session["tools"])


def test_openai_mic_is_resampled_to_24khz():
    ws, _, _, _ = talk_openai([])
    chunk = next(e for e in ws.sent if e["type"] == "input_audio_buffer.append")
    assert len(base64.b64decode(chunk["audio"])) == 1280 * 2 * 24000 // 16000


def test_openai_reply_audio_and_transcripts_reach_speaker_and_backend():
    ws, _, player, backend = talk_openai([
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "hola robert"},
        {"type": "response.output_audio.delta", "delta": base64.b64encode(b"ab").decode()},
        {"type": "response.output_audio_transcript.delta", "delta": "¡Hola!"},
        {"type": "response.done", "response": {"status": "completed", "output": []}}])
    assert player.written == [b"ab"]
    assert ("listening", "hola robert", "¡Hola!") in backend.reports


def test_openai_tools_run_and_answer_with_the_photo():
    ws, _, _, backend = talk_openai([done(("parar", {}), ("mirar", {}))], camera=lambda: b"\xff\xd8jpeg")
    assert backend.commands == ["stop"]
    outputs = [e["item"] for e in ws.sent if e["type"] == "conversation.item.create"]
    assert outputs[0] == {"type": "function_call_output", "call_id": "c0", "output": json.dumps({"ok": True})}
    assert outputs[1]["call_id"] == "c1"
    image = outputs[2]["content"][1]
    assert image["type"] == "input_image"
    assert base64.b64decode(image["image_url"].split(",", 1)[1]) == b"\xff\xd8jpeg"
    assert ws.kinds().index("response.create") > ws.kinds().index("conversation.item.create")


def test_openai_opening_line_is_sent_as_text_then_answered():
    ws = FakeWS([])
    provider, _ = openai(ws)
    talk = LiveConversation(provider, "p", FakeMic(), FakePlayer(), NS(is_speech=lambda f: False),
                            FakeBackend(), 0.3, opening="Saluda")
    asyncio.run(asyncio.wait_for(talk.run(), 5))
    assert ws.sent[1]["item"]["content"][0]["text"] == "Saluda"
    assert ws.sent[2]["type"] == "response.create"


def test_openai_quota_error_ends_the_talk_and_reads_as_no_credit():
    with pytest.raises(RuntimeError) as err:
        talk_openai([{"type": "error", "error": {"type": "insufficient_quota", "code": "insufficient_quota",
                                                  "message": "You exceeded your current quota"}}])
    assert OpenAI.out_of_credit(err.value)


def test_openai_harmless_errors_are_only_logged():
    ws, _, _, _ = talk_openai([{"type": "error", "error": {"type": "invalid_request_error",
                                                            "code": "response_cancel_not_active"}}])
    assert ws.sent[0]["type"] == "session.update"


def test_openai_bad_tool_arguments_become_empty():
    ws, _, _, backend = talk_openai([{"type": "response.done", "response": {"output": [
        {"type": "function_call", "call_id": "x", "name": "mover", "arguments": "{not json"}]}}])
    out = next(e["item"] for e in ws.sent if e["type"] == "conversation.item.create")
    assert json.loads(out["output"])["ok"] is False and backend.commands == []


def test_make_picks_the_provider_and_its_defaults():
    p = providers.make("ChatGPT", "sk-x")
    assert isinstance(p, OpenAI) and p.model == "gpt-realtime" and p.voice == "marin"
    assert providers.make("openai", "sk-x", "gpt-realtime-mini", "cedar").voice == "cedar"
    with pytest.raises(ValueError, match="AI_PROVIDER"):
        providers.make("claude", "k")


def test_settings_read_the_generic_key_and_still_accept_old_gemini_names(monkeypatch, tmp_path):
    for k in ("AI_PROVIDER", "AI_API_KEY", "AI_MODEL", "AI_VOICE", "GEMINI_API_KEY", "OPENAI_API_KEY",
              "VOICE_LIVE_MODEL", "VOICE_VOICE", "OWNER_NAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "old-key")
    monkeypatch.setenv("VOICE_VOICE", "Kore")
    s = VoiceSettings.from_env(None)
    assert (s.ai_provider, s.ai_api_key, s.ai_voice) == ("gemini", "old-key", "Kore")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_API_KEY", "sk-new")
    monkeypatch.setenv("OWNER_NAME", " Ana ")
    s = VoiceSettings.from_env(None)
    assert (s.ai_provider, s.ai_api_key, s.ai_voice, s.owner_name) == ("openai", "sk-new", "", "Ana")


def test_session_config_has_no_empty_parameters():
    cfg = session_config("p", "marin", 500, [{"name": "parar", "description": "d", "parameters": None}])
    assert cfg["tools"][0]["parameters"] == {"type": "object", "properties": {}}
    assert cfg["audio"]["input"]["format"]["rate"] == 24000
