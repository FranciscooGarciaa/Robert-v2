"""Microphone and speaker I/O through ALSA command-line tools.

`arecord`/`aplay` on the ReSpeaker card avoid PyAudio/portaudio builds and
keep capture and playback in separate, restartable processes.
"""

import io
import logging
import queue
import subprocess
import threading
import wave

import numpy as np

log = logging.getLogger("robert.voice.audio")

RATE = 16000
FRAME = 1280                 # 80 ms: openWakeWord's native frame
STARTUP_DISCARD_S = 0.5      # the codec ADC thumps when capture starts


class Mic:
    """Continuous 16 kHz mono capture; frames of FRAME int16 samples.

    A reader thread always drains arecord (so it never overruns); while
    muted (Robert is speaking) frames are dropped instead of queued.
    """

    def __init__(self, device: str):
        self._device = device
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._muted = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._thread = threading.Thread(target=self._run, name="mic", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        nbytes = FRAME * 2
        while True:
            self._proc = subprocess.Popen(
                ["arecord", "-q", "-D", self._device, "-f", "S16_LE", "-r", str(RATE),
                 "-c", "1", "-t", "raw"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            skip = int(STARTUP_DISCARD_S * RATE / FRAME)
            stream = self._proc.stdout
            while (chunk := stream.read(nbytes)) and len(chunk) == nbytes:
                if skip:
                    skip -= 1
                    continue
                if self._muted.is_set():
                    continue
                try:
                    self._q.put_nowait(np.frombuffer(chunk, dtype=np.int16))
                except queue.Full:  # consumer stalled (e.g. waiting on the API)
                    self._q.get_nowait()
            log.warning("arecord stopped (rc=%s); restarting", self._proc.poll())
            self._proc.kill()

    def read(self, timeout: float | None = None) -> np.ndarray:
        return self._q.get(timeout=timeout)

    def mute(self, on: bool) -> None:
        if on:
            self._muted.set()
        else:
            self.flush()
            self._muted.clear()

    def flush(self) -> None:
        while not self._q.empty():
            self._q.get_nowait()


def to_wav(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def tone(freq: float, seconds: float, level: float = 0.25, rate: int = 24000) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    ramp = np.minimum(1, np.minimum(t, seconds - t) / 0.01)
    return to_wav((level * 32767 * ramp * np.sin(2 * np.pi * freq * t)).astype(np.int16).tobytes(), rate)


class Player:
    """Plays WAV bytes one at a time on the ALSA device."""

    def __init__(self, device: str):
        self._device = device
        self._lock = threading.Lock()

    def play(self, wav: bytes) -> None:
        with self._lock:
            try:
                subprocess.run(["aplay", "-q", "-D", self._device, "-"], input=wav,
                               timeout=60, stderr=subprocess.DEVNULL, check=False)
            except subprocess.TimeoutExpired:
                log.warning("aplay timed out")
