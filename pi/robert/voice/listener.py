"""Local wake word (openWakeWord) and voice-activity check (webrtcvad).

Only the wake word runs all the time; once awake, the conversation streams
to Gemini Live, whose server-side VAD decides when a turn ends. The local
VAD here just timestamps speech for latency logs and the idle timeout.
"""

import logging

import numpy as np
import webrtcvad

from .audio import MIC_RATE, Mic

log = logging.getLogger("robert.voice.listen")

VAD_MS = 20
VAD_BYTES = MIC_RATE * VAD_MS // 1000 * 2


class Vad:
    def __init__(self, aggressiveness: int = 2):
        self._vad = webrtcvad.Vad(aggressiveness)

    def is_speech(self, frame: np.ndarray) -> bool:
        """True if at least two 20 ms slices of the 80 ms frame hold speech."""
        raw = frame.tobytes()
        voiced = sum(self._vad.is_speech(raw[i:i + VAD_BYTES], MIC_RATE)
                     for i in range(0, len(raw) - VAD_BYTES + 1, VAD_BYTES))
        return voiced >= 2


class WakeWord:
    def __init__(self, mic: Mic, model: str, threshold: float):
        from openwakeword.model import Model  # heavy import, only in the service

        self._mic = mic
        self._threshold = threshold
        self._oww = Model(wakeword_models=[model], inference_framework="onnx")

    def wait(self) -> float:
        """Blocks until the wake word is heard; returns its score."""
        self._oww.reset()
        self._mic.flush()
        while True:
            frame = self._mic.read(timeout=1.0)
            if frame is None:
                continue
            best = max(self._oww.predict(frame).values())
            if best > self._threshold:
                log.info("wake word (score %.2f)", best)
                return best
