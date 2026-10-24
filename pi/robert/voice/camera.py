"""Still photos from the front camera (OV5647) for Robert to look at.

The vision service owns the camera and keeps the newest frame, so a photo is
instant; if it is not running, the camera is free and rpicam-still is used.
"""

import logging
import subprocess
import urllib.request

log = logging.getLogger("robert.voice.camera")

WIDTH, HEIGHT = 640, 480   # plenty for the model, small to upload (~40 kB)
TIMEOUT_S = 5
VISION_PHOTO = "http://127.0.0.1:8002/photo.jpg"


def capture_jpeg() -> bytes | None:
    """One JPEG, or None if the camera is missing or busy."""
    try:
        with urllib.request.urlopen(VISION_PHOTO, timeout=2) as r:
            return r.read()
    except OSError:
        pass  # vision service down: take the camera directly (~0.8 s)
    try:
        result = subprocess.run(
            ["rpicam-still", "-n", "--immediate", "-t", "500", "--width", str(WIDTH),
             "--height", str(HEIGHT), "-q", "80", "-e", "jpg", "-o", "-"],
            capture_output=True, timeout=TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("camera capture failed: %r", exc)
        return None
    if result.returncode != 0 or not result.stdout.startswith(b"\xff\xd8"):
        log.warning("camera capture failed (rc=%s)", result.returncode)
        return None
    return result.stdout
