"""OpenAI Realtime (the speech-to-speech model behind ChatGPT's voice).

Needs an API key from platform.openai.com with credit on it: a ChatGPT Plus
subscription is not an API key. Plain WebSocket (the `websockets` package
google-genai already installs), no extra SDK. Audio is PCM16 at 24 kHz both
ways, so the 16 kHz microphone frames are resampled on the way out; replies
already match the speaker.

The camera photo cannot ride inside a function result here: the result says
the photo follows, and the image goes in as a user message right after it.
"""

import base64
import json
import logging
from contextlib import asynccontextmanager
from urllib.parse import quote

from ..audio import MIC_RATE, OUT_RATE, resample
from . import ToolCall

log = logging.getLogger("robert.voice.openai")

URL = "wss://api.openai.com/v1/realtime?model={model}"
TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
# Errors that end the conversation; the rest (e.g. a cancel with nothing to cancel) are logged.
FATAL = ("insufficient_quota", "invalid_api_key", "model_not_found", "session_expired",
         "rate_limit_exceeded")


def session_config(instruction: str, voice: str, silence_ms: int, tools: list[dict]) -> dict:
    pcm = {"type": "audio/pcm", "rate": OUT_RATE}
    return {
        "type": "realtime",
        "instructions": instruction,
        "output_modalities": ["audio"],
        "audio": {
            "input": {"format": pcm,
                      "transcription": {"model": TRANSCRIBE_MODEL, "language": "es"},
                      "turn_detection": {"type": "server_vad", "silence_duration_ms": silence_ms,
                                         "prefix_padding_ms": 100}},
            "output": {"format": pcm, "voice": voice},
        },
        "tools": [{"type": "function", "name": t["name"], "description": t["description"],
                   "parameters": t.get("parameters") or {"type": "object", "properties": {}}}
                  for t in tools],
        "tool_choice": "auto",
    }


class OpenAI:
    default_model = "gpt-realtime"
    default_voice = "marin"

    def __init__(self, api_key: str, model: str, voice: str, silence_ms: int, dial=None):
        if dial is None:
            from websockets.asyncio.client import connect as dial
        self._dial = dial
        self._key = api_key
        self.model, self.voice, self.silence_ms = model, voice, silence_ms

    @asynccontextmanager
    async def connect(self, instruction: str):
        from ..live import TOOLS

        async with self._dial(URL.format(model=quote(self.model)), max_size=None,
                              additional_headers={"Authorization": f"Bearer {self._key}"}) as ws:
            link = OpenAILink(ws)
            await link.send({"type": "session.update",
                             "session": session_config(instruction, self.voice, self.silence_ms, TOOLS)})
            yield link

    @staticmethod
    def out_of_credit(exc: Exception) -> bool:
        return "insufficient_quota" in str(exc)


class OpenAILink:
    def __init__(self, ws):
        self._ws = ws

    async def send(self, event: dict) -> None:
        await self._ws.send(json.dumps(event, ensure_ascii=False))

    async def say(self, text: str) -> None:
        await self.send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}})
        await self.send({"type": "response.create"})

    async def send_audio(self, frame) -> None:
        pcm = resample(frame.tobytes(), MIC_RATE, OUT_RATE)
        await self.send({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm).decode()})

    async def events(self):
        async for raw in self._ws:
            ev = json.loads(raw)
            kind = ev.get("type", "")
            if kind == "error":
                err = ev.get("error") or {}
                text = f"{err.get('code') or err.get('type')}: {err.get('message')}"
                if err.get("code") in FATAL:
                    raise RuntimeError(text)
                log.warning("realtime error %s", text)
            elif kind == "conversation.item.input_audio_transcription.completed":
                yield "heard", ev.get("transcript") or ""
            elif kind in ("response.output_audio.delta", "response.audio.delta"):
                yield "audio", base64.b64decode(ev["delta"])
            elif kind in ("response.output_audio_transcript.delta", "response.audio_transcript.delta"):
                yield "said", ev.get("delta") or ""
            elif kind == "response.done":
                response = ev.get("response") or {}
                if response.get("status") == "failed":
                    detail = (response.get("status_details") or {}).get("error") or {}
                    raise RuntimeError(f"{detail.get('code') or detail.get('type')}: {detail.get('message')}")
                yield "turn_done", None
                calls = [ToolCall(item.get("call_id", ""), item.get("name", ""), _args(item.get("arguments")))
                         for item in response.get("output") or [] if item.get("type") == "function_call"]
                if calls:
                    yield "tools", calls
        yield "close", None

    async def answer(self, results) -> None:
        for call, result, jpeg in results:
            await self.send({"type": "conversation.item.create", "item": {
                "type": "function_call_output", "call_id": call.id,
                "output": json.dumps(result, ensure_ascii=False)}})
            if jpeg is not None:
                await self.send({"type": "conversation.item.create", "item": {
                    "type": "message", "role": "user", "content": [
                        {"type": "input_text", "text": "La foto de tu cámara delantera:"},
                        {"type": "input_image",
                         "image_url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}]}})
        await self.send({"type": "response.create"})


def _args(raw) -> dict:
    try:
        args = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return args if isinstance(args, dict) else {}
