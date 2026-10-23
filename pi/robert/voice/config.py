"""Voice service settings, from the same pi/.env as the backend."""

import os
from dataclasses import dataclass
from pathlib import Path

from ..config import ROOT, _load_dotenv

HOME = Path.home()


@dataclass(frozen=True)
class VoiceSettings:
    gemini_api_key: str = ""
    model: str = "gemini-3.5-flash-lite"         # understands the audio and replies
    tts_model: str = "gemini-3.8-flash-lite-tts"
    tts_voice: str = "Puck"
    tts_timeout_s: float = 10.0                  # Gemini rejects deadlines under 10 s
    model_timeout_s: float = 15.0
    wake_model: str = "hey_jarvis"               # bundled name or path to a custom .onnx
    wake_threshold: float = 0.3
    piper_voice: Path = HOME / "robert-models/piper/es_ES-davefx-medium.onnx"
    alsa_device: str = "plughw:CARD=seeed2micvoicec"
    backend_url: str = "http://127.0.0.1:8000"

    @classmethod
    def from_env(cls, dotenv: Path | None = ROOT / ".env") -> "VoiceSettings":
        if dotenv:
            _load_dotenv(dotenv)
        e = os.environ
        return cls(
            gemini_api_key=e.get("GEMINI_API_KEY", cls.gemini_api_key),
            model=e.get("VOICE_MODEL", cls.model),
            tts_model=e.get("VOICE_TTS_MODEL", cls.tts_model),
            tts_voice=e.get("VOICE_TTS_VOICE", cls.tts_voice),
            tts_timeout_s=float(e.get("VOICE_TTS_TIMEOUT_S", cls.tts_timeout_s)),
            model_timeout_s=float(e.get("VOICE_MODEL_TIMEOUT_S", cls.model_timeout_s)),
            wake_model=e.get("WAKE_MODEL", cls.wake_model),
            wake_threshold=float(e.get("WAKE_THRESHOLD", cls.wake_threshold)),
            piper_voice=Path(e.get("PIPER_VOICE", cls.piper_voice)),
            alsa_device=e.get("VOICE_ALSA_DEVICE", cls.alsa_device),
            backend_url=e.get("VOICE_BACKEND_URL", cls.backend_url),
        )
