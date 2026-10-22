"""Backend Esp32Client against the simulated ESP32, over real WebSockets."""

import asyncio
import contextlib

from robert.config import Settings
from robert.robot.esp32_client import Esp32Client
from robert.robot.motion import MotionController
from robert.robot.state import RobotState
from robert.sim.fake_esp32 import FakeEsp32

PORT = 18181


async def wait_for(cond, timeout=3.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


@contextlib.asynccontextmanager
async def rig(token="dev", client_token="dev", port=PORT):
    fake = FakeEsp32(token)
    server = await fake.serve("127.0.0.1", port)
    settings = Settings(esp32_host="127.0.0.1", esp32_port=port, esp32_token=client_token)
    state = RobotState()
    motion = MotionController(state, settings.max_speed, settings.deadman_ms)
    client = Esp32Client(settings, state, motion)
    task = asyncio.create_task(client.run())
    try:
        yield fake, state, motion, server
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        fake._tick_task.cancel()
        server.close()
        await server.wait_closed()


def test_connects_streams_moves_and_telemetry():
    async def run():
        async with rig() as (fake, state, motion, _):
            await wait_for(lambda: state.esp32.connected)
            motion.drive(80, 80, "pwa-1")
            await wait_for(lambda: fake.req == (80, 80))
            await wait_for(lambda: state.esp32.left_out == 80)
            assert state.ultrasonic.distance_cm == 80.0
            motion.stop("test")
            await wait_for(lambda: fake.req == (0, 0))

    asyncio.run(run())


def test_deadman_on_pi_stops_esp32():
    async def run():
        async with rig() as (fake, state, motion, _):
            await wait_for(lambda: state.esp32.connected)
            motion.drive(100, 100, "pwa-1")
            await wait_for(lambda: fake.req == (100, 100))
            # No refresh from the PWA: Pi dead-man (300 ms) must zero it.
            await wait_for(lambda: fake.req == (0, 0), timeout=1.0)

    asyncio.run(run())


def test_bad_token_never_connects():
    async def run():
        async with rig(client_token="wrong") as (fake, state, motion, _):
            await asyncio.sleep(0.5)
            assert not state.esp32.connected
            assert fake.controller is None

    asyncio.run(run())


def test_link_loss_stops_motion_and_reconnects():
    async def run():
        async with rig() as (fake, state, motion, _):
            await wait_for(lambda: state.esp32.connected)
            motion.drive(60, 60, "pwa-1")
            await wait_for(lambda: fake.req == (60, 60))
            await fake.controller.close()
            await wait_for(lambda: state.motion.target_left == 0)
            assert state.motion.last_stop_reason == "esp32_disconnected"
            await wait_for(lambda: state.esp32.connected and state.esp32.reconnects == 2)

    asyncio.run(run())
