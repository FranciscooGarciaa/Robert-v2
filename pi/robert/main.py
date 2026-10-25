"""Robert high-level backend: one process, ESP32 link + PWA API.

    python -m robert.main            (from pi/, reads pi/.env)
"""

import asyncio
import contextlib
import json
import logging
import threading
import time
import urllib.request
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import http, ws
from .config import Settings
from .robot.esp32_client import Esp32Client
from .robot.lidar import LidarReader, ScanAssembler
from .nav.autonomy import TICK_S, Autonomy
from .nav.calib import load_mask, sectors
from .nav.guard import CollisionGuard
from .nav.model import RobotModel
from .nav.scan import ScanView
from .robot.motion import MotionController
from .nav.social import SocialGreeter
from .robot.dance import Dancer
from .robot.nudge import Nudger
from .robot.state import RobotState

log = logging.getLogger("robert")


VISION_URL = "http://127.0.0.1:8002"
VOICE_URL = "http://127.0.0.1:8001"
VOICE_BUSY = ("listening", "thinking", "speaking")


def greet_if_person() -> None:
    """Social mode: one camera check, then ask the voice to say hello (never blocks the loop)."""
    def call() -> None:
        try:
            with urllib.request.urlopen(f"{VISION_URL}/people_now", timeout=3) as r:
                people = json.load(r).get("people", [])
            if not people:
                return
            req = urllib.request.Request(f"{VOICE_URL}/greet", data=b"", method="POST")
            urllib.request.urlopen(req, timeout=2).close()
            log.info("social: greeting someone in front")
        except OSError as exc:
            log.warning("social greeting failed: %r", exc)

    threading.Thread(target=call, daemon=True).start()


def vision_detect(on: bool) -> None:
    """Asks the vision service to run (or stop) person detection; never blocks the loop."""
    def call() -> None:
        try:
            req = urllib.request.Request(f"{VISION_URL}/detect/{'on' if on else 'off'}", data=b"",
                                         method="POST")
            urllib.request.urlopen(req, timeout=2).close()
        except OSError as exc:
            log.warning("vision service unreachable (%r): follow cannot find people", exc)

    threading.Thread(target=call, daemon=True).start()


def create_app(settings: Settings, start_esp32: bool = True,
               start_lidar: bool = True) -> FastAPI:
    robot = RobotState()
    model = RobotModel.from_settings(settings)
    robot.nav.footprint = [model.length_m, model.width_m, model.margin_m]
    mask = load_mask(settings.lidar_mask_path, settings.lidar_offset_deg, settings.lidar_clockwise)
    robot.nav.blind = [list(s) for s in sectors(mask)]
    scan = ScanView(robot.lidar, model, settings.guard_stale_s, mask=mask)
    guard = CollisionGuard(model, scan, robot.nav, settings.guard_slow_extra_m,
                           settings.guard_blind_max_speed, settings.guard_enabled)
    motion = MotionController(robot, settings.max_speed, settings.deadman_ms, guard=guard)
    autonomy = Autonomy(settings, robot, motion, scan, model, on_follow=vision_detect)
    nudger = Nudger(motion)
    dancer = Dancer(motion)
    social = SocialGreeter()
    esp32 = Esp32Client(settings, robot, motion)
    lidar = None
    if settings.lidar_enabled and start_lidar:
        robot.lidar.enabled = True
        lidar = LidarReader(
            settings.lidar_port, settings.lidar_baud, robot.lidar,
            ScanAssembler(settings.lidar_offset_deg, settings.lidar_clockwise),
        )

    async def nav_loop() -> None:
        """Scan-rate loop: clearances for everyone, then one autonomy step."""
        while True:
            guard.observe()
            autonomy.tick()
            busy = (robot.motion.mode != "idle" or autonomy.active or dancer.active
                    or robot.voice.state in VOICE_BUSY)
            robot.nav.dancing = dancer.active
            if social.update(scan.points(), busy, time.monotonic()):
                greet_if_person()
            await asyncio.sleep(TICK_S)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(esp32.run()) if start_esp32 else None
        nav_task = asyncio.create_task(nav_loop())
        if lidar:
            lidar.start()
        log.info("backend up, ESP32 at %s", esp32.uri)
        try:
            yield
        finally:
            motion.stop("shutdown")
            nav_task.cancel()
            if lidar:
                lidar.stop()
            if task:
                # Give the sender one tick to flush the stop before closing.
                await asyncio.sleep(0.2)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="Robert", lifespan=lifespan)
    app.state.settings = settings
    app.state.robot = robot
    app.state.motion = motion
    app.state.autonomy = autonomy
    app.state.nudger = nudger
    app.state.dancer = dancer
    app.state.social = social
    app.include_router(http.router)
    app.include_router(ws.router)
    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = Settings.from_env()
    if not settings.esp32_token:
        log.warning("ESP32_TOKEN is empty; the ESP32 will reject the handshake")
    app = create_app(settings)
    servers = [uvicorn.Server(uvicorn.Config(app, host=settings.http_host, port=settings.http_port,
                                             log_level="warning"))]
    cert, key = settings.certs_dir / "server.crt", settings.certs_dir / "server.key"
    if cert.is_file() and key.is_file():
        # Same app over HTTPS; lifespan off so the ESP32 link, LiDAR, etc. start only once.
        servers.append(uvicorn.Server(uvicorn.Config(
            app, host=settings.http_host, port=settings.https_port, log_level="warning",
            ssl_certfile=str(cert), ssl_keyfile=str(key), lifespan="off")))
        log.info("HTTPS on port %d", settings.https_port)

    async def serve() -> None:
        await asyncio.gather(*(s.serve() for s in servers))

    asyncio.run(serve())


if __name__ == "__main__":
    main()
