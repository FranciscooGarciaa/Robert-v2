"""Robert high-level backend: one process, ESP32 link + PWA API.

    python -m robert.main            (from pi/, reads pi/.env)
"""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import http, ws
from .config import Settings
from .robot.esp32_client import Esp32Client
from .robot.lidar import LidarReader, ScanAssembler
from .robot.motion import MotionController
from .robot.state import RobotState

log = logging.getLogger("robert")


def create_app(settings: Settings, start_esp32: bool = True,
               start_lidar: bool = True) -> FastAPI:
    robot = RobotState()
    motion = MotionController(robot, settings.max_speed, settings.deadman_ms)
    esp32 = Esp32Client(settings, robot, motion)
    lidar = None
    if settings.lidar_enabled and start_lidar:
        robot.lidar.enabled = True
        lidar = LidarReader(
            settings.lidar_port, settings.lidar_baud, robot.lidar,
            ScanAssembler(settings.lidar_offset_deg, settings.lidar_clockwise),
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(esp32.run()) if start_esp32 else None
        if lidar:
            lidar.start()
        log.info("backend up, ESP32 at %s", esp32.uri)
        try:
            yield
        finally:
            motion.stop("shutdown")
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
    uvicorn.run(create_app(settings), host=settings.http_host, port=settings.http_port,
                log_level="warning")


if __name__ == "__main__":
    main()
