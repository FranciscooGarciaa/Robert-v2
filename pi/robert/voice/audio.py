"""Microphone and speaker I/O through ALSA command-line tools.

`arecord`/`aplay` on the ReSpeaker card avoid PyAudio/portaudio builds and
keep capture and playback in separate, restartable processes.
"""

import logging
import queue
import subprocess
import threading
import time

import numpy as np

log = logging.getLogger("robert.voice.audio")

MIC_RATE = 16000
FRAME = 1280                 # 80 ms at 16 kHz: openWakeWord's native frame
STARTUP_DISCARD_S = 0.5      # the codec ADC thumps when capture starts
OUT_RATE = 24000             # Gemini Live speaks 24 kHz mono PCM16
OUT_BUFFER_S = 0.1           # aplay buffer: small, so audio starts quickly


class Mic:
    """Continuous 16 kHz mono capture; frames of FRAME int16 samples.

    A reader thread always drains arecord (so it never overruns); while
    muted (Robert is speaking) frames are dropped instead of queued.
    """

    def __init__(self, device: str):
        self._device = device
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._muted = threading.Event()
        self._thread = threading.Thread(target=self._run, name="mic", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        nbytes = FRAME * 2
        while True:
            proc = subprocess.Popen(
                ["arecord", "-q", "-D", self._device, "-f", "S16_LE", "-r", str(MIC_RATE),
                 "-c", "1", "-t", "raw"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            skip = int(STARTUP_DISCARD_S * MIC_RATE / FRAME)
            while (chunk := proc.stdout.read(nbytes)) and len(chunk) == nbytes:
                if skip:
                    skip -= 1
                    continue
                if self._muted.is_set():
                    continue
                try:
                    self._q.put_nowait(np.frombuffer(chunk, dtype=np.int16))
                except queue.Full:  # nobody reading: keep only the latest audio
                    self._q.get_nowait()
            log.warning("arecord stopped (rc=%s); restarting", proc.poll())
            proc.kill()
            time.sleep(0.5)

    def read(self, timeout: float | None = None) -> np.ndarray | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def mute(self, on: bool) -> None:
        if on:
            self._muted.set()
        else:
            self.flush()
            self._muted.clear()

    def flush(self) -> None:
        while not self._q.empty():
            self._q.get_nowait()


class StreamPlayer:
    """One long-lived `aplay` fed raw 24 kHz PCM as it arrives.

    Writes go through a thread (a full pipe must never block the event loop).
    `busy` stays true until the estimated end of what was written, so the
    caller knows when the speaker has really gone quiet.
    """

    def __init__(self, device: str):
        self._device = device
        self._q: queue.Queue[bytes] = queue.Queue()
        self._ends_at = 0.0
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        threading.Thread(target=self._run, name="player", daemon=True).start()

    def _open(self) -> subprocess.Popen:
        return subprocess.Popen(
            ["aplay", "-q", "-D", self._device, "-t", "raw", "-f", "S16_LE", "-r", str(OUT_RATE),
             "-c", "1", f"--buffer-time={int(OUT_BUFFER_S * 1e6)}"],
            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def _run(self) -> None:
        while True:
            chunk = self._q.get()
            if self._proc is None or self._proc.poll() is not None:
                self._proc = self._open()
            try:
                self._proc.stdin.write(chunk)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError):
                log.warning("aplay died; reopening")
                self._proc = None

    def write(self, pcm: bytes) -> None:
        with self._lock:
            now = time.monotonic()
            self._ends_at = max(now, self._ends_at) + len(pcm) / (2 * OUT_RATE)
        self._q.put(pcm)

    @property
    def busy(self) -> bool:
        return time.monotonic() < self._ends_at + OUT_BUFFER_S

    def remaining_s(self) -> float:
        return max(0.0, self._ends_at + OUT_BUFFER_S - time.monotonic())


def tone(freq: float, seconds: float, level: float = 0.25) -> bytes:
    """A short sine beep as 24 kHz PCM16."""
    t = np.arange(int(seconds * OUT_RATE)) / OUT_RATE
    ramp = np.minimum(1, np.minimum(t, seconds - t) / 0.01)
    return (level * 32767 * ramp * np.sin(2 * np.pi * freq * t)).astype(np.int16).tobytes()


def resample(pcm: bytes, rate_in: int, rate_out: int = OUT_RATE) -> bytes:
    x = np.frombuffer(pcm, dtype=np.int16)
    if rate_in == rate_out or x.size == 0:
        return pcm
    t = np.arange(0, x.size / rate_in, 1 / rate_out)
    return np.interp(t, np.arange(x.size) / rate_in, x).astype(np.int16).tobytes()
