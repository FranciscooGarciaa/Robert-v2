"""Voice service settings, from the same pi/.env as the backend."""

import os
from dataclasses import dataclass
from pathlib import Path

from ..config import ROOT, _load_dotenv

HOME = Path.home()


@dataclass(frozen=True)
class VoiceSettings:
    gemini_api_key: str = ""
    # Speech-to-speech model; measured on the Pi 2026-10-23: ~0.5 s connect,
    # 0.6-0.8 s from the end of speech to the first audio of the reply.
    live_model: str = "gemini-3.8-live"
    voice: str = "Puck"
    # Server VAD: this much silence ends your turn. 300 ms with high sensitivity cut
    # people off at their first pause ("Quiero..."), and the rest was lost while
    # Robert answered (half-duplex), so: 600 ms, low end-of-speech sensitivity.
    silence_ms: int = 600
    idle_s: float = 10.0            # no speech for this long ends the conversation
    wake_model: str = "hey_jarvis"  # bundled name or path to a custom .onnx
    wake_threshold: float = 0.3
    piper_voice: Path = HOME / "robert-models/piper/es_ES-davefx-medium.onnx"
    assets_dir: Path = HOME / "robert-models/voice"
    alsa_device: str = "plughw:CARD=seeed2micvoicec"
    backend_url: str = "http://127.0.0.1:8000"

    @classmethod
    def from_env(cls, dotenv: Path | None = ROOT / ".env") -> "VoiceSettings":
        if dotenv:
            _load_dotenv(dotenv)
        e = os.environ
        return cls(
            gemini_api_key=e.get("GEMINI_API_KEY", cls.gemini_api_key),
            live_model=e.get("VOICE_LIVE_MODEL", cls.live_model),
            voice=e.get("VOICE_VOICE", cls.voice),
            silence_ms=int(e.get("VOICE_SILENCE_MS", cls.silence_ms)),
            idle_s=float(e.get("VOICE_IDLE_S", cls.idle_s)),
            wake_model=e.get("WAKE_MODEL", cls.wake_model),
            wake_threshold=float(e.get("WAKE_THRESHOLD", cls.wake_threshold)),
            piper_voice=Path(e.get("PIPER_VOICE", cls.piper_voice)),
            assets_dir=Path(e.get("VOICE_ASSETS_DIR", cls.assets_dir)),
            alsa_device=e.get("VOICE_ALSA_DEVICE", cls.alsa_device),
            backend_url=e.get("VOICE_BACKEND_URL", cls.backend_url),
        )
