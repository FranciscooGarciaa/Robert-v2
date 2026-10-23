"""Pre-rendered sounds, so nothing heavy stays loaded just in case.

Phrases are synthesised once with Piper (local, offline) and cached as raw
24 kHz PCM next to the models; later runs only read the file.
"""

import io
import logging
import wave
from pathlib import Path

from .audio import resample, tone

log = logging.getLogger("robert.voice.assets")

PHRASES = {
    "no_network": "Ahora mismo no tengo conexión, perdona.",
}


def beep() -> bytes:
    return tone(880, 0.12)


def phrase(name: str, assets_dir: Path, piper_voice: Path) -> bytes:
    path = assets_dir / f"{name}.pcm"
    if path.is_file():
        return path.read_bytes()
    try:
        from piper import PiperVoice

        voice = PiperVoice.load(str(piper_voice))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            voice.synthesize_wav(PHRASES[name], w)
        buf.seek(0)
        with wave.open(buf) as w:
            pcm = resample(w.readframes(w.getnframes()), w.getframerate())
    except Exception as exc:  # noqa: BLE001 - a missing phrase falls back to a tone
        log.warning("cannot render %s (%r); using a tone", name, exc)
        return tone(330, 0.4)
    assets_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pcm)
    return pcm
