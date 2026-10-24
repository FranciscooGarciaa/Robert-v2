"""Continuous low-rate capture from the OV5647 via rpicam-vid (raw YUV420 on stdout)."""

import logging
import subprocess
import threading
import time

import numpy as np

log = logging.getLogger("robert.vision.camera")

W, H, FPS = 640, 480, 6
FRAME_BYTES = W * H * 3 // 2


def yuv420_to_rgb(buf: bytes, w: int, h: int, step: int = 1) -> np.ndarray:
    """I420 -> RGB uint8 (BT.601). step=2 halves the size (and skips upsampling chroma)."""
    a = np.frombuffer(buf, dtype=np.uint8)
    y = a[:w * h].reshape(h, w)[::step, ::step].astype(np.float32)
    u = a[w * h:w * h * 5 // 4].reshape(h // 2, w // 2).astype(np.float32) - 128
    v = a[w * h * 5 // 4:].reshape(h // 2, w // 2).astype(np.float32) - 128
    if step == 1:
        u = u.repeat(2, axis=0).repeat(2, axis=1)
        v = v.repeat(2, axis=0).repeat(2, axis=1)
    rgb = np.stack([y + 1.402 * v, y - 0.344 * u - 0.714 * v, y + 1.772 * u], axis=-1)
    return np.clip(rgb, 0, 255).astype(np.uint8)


class Camera:
    """Keeps only the newest frame; readers never wait for the camera."""

    def __init__(self):
        self._frame: bytes | None = None
        self._t = 0.0
        self._lock = threading.Lock()
        threading.Thread(target=self._run, name="camera", daemon=True).start()

    def _run(self) -> None:
        while True:
            proc = subprocess.Popen(
                ["rpicam-vid", "-n", "-t", "0", "--codec", "yuv420", "--width", str(W),
                 "--height", str(H), "--framerate", str(FPS), "-o", "-"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            while (buf := proc.stdout.read(FRAME_BYTES)) and len(buf) == FRAME_BYTES:
                with self._lock:
                    self._frame, self._t = buf, time.monotonic()
            log.warning("rpicam-vid stopped (rc=%s); restarting", proc.poll())
            proc.kill()
            time.sleep(2)

    def latest(self) -> tuple[bytes | None, float]:
        with self._lock:
            return self._frame, self._t
