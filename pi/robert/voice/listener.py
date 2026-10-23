"""Wake word + utterance capture (pattern and constants from the Cuántico project).

openWakeWord scores every 80 ms frame; after a wake, webrtcvad records until
the speaker has talked for MIN_VOICE_MS and then stays silent for
SILENCE_MS_TO_STOP, with a hard cap of MAX_UTTERANCE_MS.
"""

import logging
import time
from collections import deque

import numpy as np
import webrtcvad

from .audio import FRAME, RATE, Mic

log = logging.getLogger("robert.voice.listen")

FRAME_MS = FRAME * 1000 // RATE          # 80
VAD_MS = 20
VAD_SAMPLES = RATE * VAD_MS // 1000      # 320
SILENCE_MS_TO_STOP = 400
MIN_VOICE_MS = 300
MAX_UTTERANCE_MS = 8000
PRE_ROLL_FRAMES = 3                      # keep ~240 ms before speech starts


def _load_wake_model(name: str):
    from openwakeword.model import Model  # heavy import, only in the service

    return Model(wakeword_models=[name], inference_framework="onnx")


class Listener:
    def __init__(self, mic: Mic, wake_model: str, threshold: float):
        self._mic = mic
        self._threshold = threshold
        self._oww = _load_wake_model(wake_model)
        self._vad = webrtcvad.Vad(2)

    def wait_wake(self) -> float:
        """Blocks until the wake word is heard; returns its score."""
        self._oww.reset()
        while True:
            scores = self._oww.predict(self._mic.read())
            best = max(scores.values())
            if best > self._threshold:
                log.info("wake word (score %.2f)", best)
                return best

    def _voiced_ms(self, frame: np.ndarray) -> int:
        raw = frame.tobytes()
        step = VAD_SAMPLES * 2
        return sum(VAD_MS for i in range(0, len(raw), step)
                   if self._vad.is_speech(raw[i:i + step], RATE))

    def record(self, wait_for_voice_ms: int | None = None) -> bytes | None:
        """PCM of one utterance, or None if nobody spoke within `wait_for_voice_ms`.

        With wait_for_voice_ms=None (just after the wake word) recording starts
        at once; otherwise it waits for speech first (follow-up turns).
        """
        pre = deque(maxlen=PRE_ROLL_FRAMES)
        if wait_for_voice_ms is not None:
            waited = 0
            while True:
                frame = self._mic.read()
                pre.append(frame)
                if self._voiced_ms(frame) >= VAD_MS * 2:
                    break
                waited += FRAME_MS
                if waited >= wait_for_voice_ms:
                    return None
        out = list(pre)
        voice = sum(self._voiced_ms(f) for f in out)
        silence = 0
        start = time.monotonic()
        while (time.monotonic() - start) * 1000 < MAX_UTTERANCE_MS:
            frame = self._mic.read()
            out.append(frame)
            v = self._voiced_ms(frame)
            voice += v
            silence = 0 if v else silence + FRAME_MS
            if voice >= MIN_VOICE_MS and silence >= SILENCE_MS_TO_STOP:
                break
        if voice < MIN_VOICE_MS:
            return None
        return np.concatenate(out).tobytes()
