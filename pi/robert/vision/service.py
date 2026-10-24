"""Robert vision service: sole owner of the camera.

    python -m robert.vision.service       (from pi/, reads pi/.env)

Always keeps the newest frame (6 fps) so the voice can ask for a photo
(GET 127.0.0.1:8002/photo.jpg). Person detection only runs while the backend
asks for it (POST /detect/on|off, during follow-me); each result is posted
to the backend as directions in the robot frame.
"""

import io
import json
import logging
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..config import ROOT, _load_dotenv
from .camera import H, W, Camera, yuv420_to_rgb
from .detector import Detector

log = logging.getLogger("robert.vision")

PORT = 8002
BACKEND = "http://127.0.0.1:8000"
MODEL = Path.home() / "robert-models/vision/yolo11n-320.onnx"
HFOV_DEG = 54.0   # OV5647 standard lens; check against the LiDAR (see README)


class Vision:
    def __init__(self, model: Path, hfov_deg: float):
        self.camera = Camera()
        self.detector = Detector(str(model))
        self.hfov = hfov_deg
        self.detecting = threading.Event()

    def photo_jpeg(self) -> bytes | None:
        from PIL import Image

        frame, t = self.camera.latest()
        if frame is None or time.monotonic() - t > 2:
            return None
        buf = io.BytesIO()
        Image.fromarray(yuv420_to_rgb(frame, W, H)).save(buf, "JPEG", quality=80)
        return buf.getvalue()

    def people(self, frame: bytes) -> list[dict]:
        """Directions of detected people; + = left, like the rest of the robot frame."""
        rgb = yuv420_to_rgb(frame, W, H, step=2)            # 320x240 is all the model needs
        w = rgb.shape[1]
        out = []
        for x1, _, x2, _, score in self.detector.people(rgb):
            cx = (x1 + x2) / 2
            out.append({"bearing_deg": round(-(cx / w - 0.5) * self.hfov, 1),
                        "half_width_deg": round((x2 - x1) / w * self.hfov / 2, 1),
                        "score": round(score, 2)})
        return out

    def detect_loop(self) -> None:
        last = 0.0
        while True:
            self.detecting.wait()
            frame, t = self.camera.latest()
            if frame is None or t == last:
                time.sleep(0.02)
                continue
            last = t
            people = self.people(frame)
            post(f"{BACKEND}/api/vision/people", {"people": people})


def post(url: str, body: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=1).close()
    except OSError:
        pass  # backend restarting


def serve(vision: Vision) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/photo.jpg":
                return self.send_error(404)
            jpeg = vision.photo_jpeg()
            if jpeg is None:
                return self.send_error(503, "no camera frame")
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpeg)))
            self.end_headers()
            self.wfile.write(jpeg)

        def do_POST(self):
            if self.path == "/detect/on":
                vision.detecting.set()
            elif self.path == "/detect/off":
                vision.detecting.clear()
            else:
                return self.send_error(404)
            log.info("person detection %s", self.path.rsplit("/", 1)[1])
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):  # quiet
            pass

    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _load_dotenv(ROOT / ".env")
    vision = Vision(Path(os.environ.get("VISION_MODEL", MODEL)),
                    float(os.environ.get("CAMERA_HFOV_DEG", HFOV_DEG)))
    threading.Thread(target=vision.detect_loop, name="detect", daemon=True).start()
    log.info("vision ready on 127.0.0.1:%d", PORT)
    serve(vision)


if __name__ == "__main__":
    main()
