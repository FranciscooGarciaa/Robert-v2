"""Robert's voice: Gemini TTS, falling back to Piper (local, offline)."""

import io
import logging
import re
import wave
from pathlib import Path

from .audio import Player, to_wav

log = logging.getLogger("robert.voice.speak")

TTS_STYLE = "Dilo con tono cercano, alegre y natural, a ritmo normal: "


def _pcm_rate(mime: str) -> int:
    m = re.search(r"rate=(\d+)", mime or "")
    return int(m.group(1)) if m else 24000


class Speaker:
    def __init__(self, player: Player, client, tts_model: str, voice: str,
                 timeout_s: float, piper_voice: Path):
        self._player = player
        self._client = client
        self._model = tts_model
        self._voice = voice
        self._timeout_ms = int(timeout_s * 1000)
        self._piper = self._load_piper(piper_voice)

    @staticmethod
    def _load_piper(path: Path):
        try:
            from piper import PiperVoice

            return PiperVoice.load(str(path))
        except Exception as exc:  # noqa: BLE001 - offline voice is optional
            log.warning("Piper unavailable (%s): no offline voice", exc)
            return None

    def gemini_wav(self, text: str) -> bytes:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self._model, contents=TTS_STYLE + text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self._voice))),
                http_options=types.HttpOptions(timeout=self._timeout_ms),
            ))
        blob = response.candidates[0].content.parts[0].inline_data
        if blob.mime_type == "audio/wav" or blob.data[:4] == b"RIFF":
            return blob.data
        return to_wav(blob.data, _pcm_rate(blob.mime_type))  # raw L16 PCM

    def piper_wav(self, text: str) -> bytes | None:
        if self._piper is None:
            return None
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            self._piper.synthesize_wav(text, w)
        return buf.getvalue()

    def synth(self, text: str, offline: bool = False) -> bytes | None:
        if not offline:
            try:
                return self.gemini_wav(text)
            except Exception as exc:  # noqa: BLE001 - any failure: use the local voice
                log.warning("Gemini TTS failed (%r); using Piper", exc)
        return self.piper_wav(text)

    def say(self, text: str, offline: bool = False) -> None:
        wav = self.synth(text, offline)
        if wav:
            self._player.play(wav)
