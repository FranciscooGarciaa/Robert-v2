"""Short, fixed choreographies for fun ("baila", "da una vuelta").

Like nudges, every step goes through MotionController at 10 Hz, so the
dead-man, the collision guard (which blocks a spin with something close)
and the ESP32 still apply. Any stop or manual drive cancels it, and no
routine lasts longer than MAX_S. Voice can only pick a routine by name.
"""

import asyncio
import logging

from .motion import MotionController

log = logging.getLogger("robert.dance")

OWNER = "dance"
PERIOD_S = 0.1
MAX_S = 6.0
SPIN_PWM = 150
# Calibrated 2026-10-24: 65 deg/s spinning at PWM 100, roughly linear in PWM.
DEG_PER_S_AT_SPIN = 65 * SPIN_PWM / 100


def _spin(deg: float, left_first: bool = True) -> tuple[int, int, float]:
    sign = 1 if left_first else -1
    return (-sign * SPIN_PWM, sign * SPIN_PWM, abs(deg) / DEG_PER_S_AT_SPIN)


WIGGLE = [(-120, 120, 0.3), (120, -120, 0.3)]
ROUTINES: dict[str, list[tuple[int, int, float]]] = {
    "baile": WIGGLE * 3 + [_spin(180), (0, 0, 0.2), _spin(180, left_first=False)],
    "vuelta": [_spin(360)],
    "celebracion": [_spin(180), (0, 0, 0.2)] + WIGGLE * 2,
}


def duration(name: str) -> float:
    return min(MAX_S, sum(step[2] for step in ROUTINES[name]))


class Dancer:
    def __init__(self, motion: MotionController):
        self._motion = motion
        self._task: asyncio.Task | None = None
        motion.on_stop(self.cancel)

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    def cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    def start(self, name: str) -> None:
        steps = ROUTINES[name]
        self.cancel()
        log.info("dance %s (%.1f s)", name, duration(name))
        self._task = asyncio.get_running_loop().create_task(self._run(steps))

    async def _run(self, steps: list[tuple[int, int, float]]) -> None:
        loop = asyncio.get_running_loop()
        hard_end = loop.time() + MAX_S
        for left, right, seconds in steps:
            end = min(loop.time() + seconds, hard_end)
            while loop.time() < end:
                self._motion.drive(left, right, owner=OWNER)
                await asyncio.sleep(PERIOD_S)
            if loop.time() >= hard_end:
                break
        self._task = None  # done on its own: stop() must not cancel us mid-call
        self._motion.release(OWNER, "dance_done")
