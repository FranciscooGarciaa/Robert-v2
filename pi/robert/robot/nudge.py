"""Short, bounded moves requested by voice ("avanza", "gira a la izquierda").

A nudge re-sends one drive command at 10 Hz for at most `NUDGE_S`, through
MotionController, so the dead-man, the collision guard and the ESP32 still
apply. Any stop (from anywhere) or a manual drive cancels it. Voice can
never start autonomy.
"""

import asyncio
import logging

from .motion import MotionController

log = logging.getLogger("robert.nudge")

NUDGE_S = 1.0
PERIOD_S = 0.1
OWNER = "voice"
SPEED = 100
TURN = 90
MOVES = {
    "forward": (SPEED, SPEED),
    "backward": (-SPEED, -SPEED),
    "left": (-TURN, TURN),
    "right": (TURN, -TURN),
}


class Nudger:
    def __init__(self, motion: MotionController):
        self._motion = motion
        self._task: asyncio.Task | None = None
        motion.on_stop(self.cancel)

    def cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    def start(self, move: str) -> None:
        """Starts a nudge; replaces one already running."""
        left, right = MOVES[move]
        self.cancel()
        log.info("voice nudge %s", move)
        self._task = asyncio.get_running_loop().create_task(self._run(left, right))

    async def _run(self, left: int, right: int) -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + NUDGE_S
        while loop.time() < end:
            self._motion.drive(left, right, owner=OWNER)
            await asyncio.sleep(PERIOD_S)
        self._task = None  # finished on its own: stop() must not cancel us mid-call
        self._motion.release(OWNER, "voice_nudge_done")
