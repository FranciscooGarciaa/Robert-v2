"""Autonomous modes, driven at the scan rate through the normal motion path.

Autonomy only runs while an operator keeps it armed: the PWA re-sends
{"type":"auto","mode":"explore"} like a dead-man. Losing that, any STOP,
a manual drive, a blind LiDAR or getting stuck disarms it. Its commands go
through MotionController, so the dead-man, the collision guard and the
ESP32 safety layers apply exactly as for manual driving.
"""

import asyncio
import logging
import math
import time
from collections.abc import Callable

from ..config import Settings
from ..robot.motion import MotionController
from ..robot.state import RobotState
from .explore import Explorer
from .model import RobotModel
from .planner import ArcPlanner
from .scan import ScanView

log = logging.getLogger("robert.auto")

MODES = ("explore",)
TICK_S = 0.1
PROGRESS_FRACTION = 0.3   # forward speed (of max) that counts as making progress


class Autonomy:
    def __init__(self, settings: Settings, state: RobotState, motion: MotionController,
                 scan: ScanView, model: RobotModel, clock: Callable[[], float] = time.monotonic):
        self._nav = state.nav
        self._motion = motion
        self._scan = scan
        self._model = model
        self._clock = clock
        self._max_pwm = settings.auto_max_speed
        self._v_max = settings.auto_max_speed * model.mps_per_pwm
        self._w_spin = 2 * settings.auto_spin_speed * model.mps_per_pwm / model.track_eff_m
        self._arm_timeout_s = settings.auto_arm_timeout_ms / 1000
        self._stuck_s = settings.auto_stuck_s
        self.planner = ArcPlanner(model)
        self.explorer = Explorer(model)
        self._owner: str | None = None
        self._armed_at = self._last_tick = self._progress_at = 0.0
        self._last_w = 0.0
        motion.on_stop(self.disarm)  # any stop, from anywhere, ends autonomy

    @property
    def active(self) -> bool:
        return self._owner is not None

    def arm(self, mode: str, owner: str) -> None:
        if mode not in MODES:
            return
        now = self._clock()
        if self._owner is None:
            log.info("autonomy %s armed by %s", mode, owner)
            self.explorer.reset()
            self._nav.stuck = False
            self._last_tick = self._progress_at = now
            self._last_w = 0.0
        self._owner = owner
        self._armed_at = now
        self._nav.auto = mode

    def disarm(self) -> None:
        if self._owner is not None:
            log.info("autonomy disarmed")
        self._owner = None
        self._nav.auto = None
        self._nav.goal_deg = None

    def release(self, owner: str) -> None:
        """The arming client went away."""
        if self._owner == owner:
            self._motion.stop("auto_disconnected")

    def tick(self) -> None:
        if self._owner is None:
            return
        now = self._clock()
        if now - self._armed_at > self._arm_timeout_s:
            self._motion.stop("auto_not_armed")
            return
        points = self._scan.points()
        if points is None:
            self._motion.stop("auto_lidar_blind")
            return

        turned = self._last_w * (now - self._last_tick)
        self._last_tick = now
        goal = self.explorer.update(points, turned)
        plan = self.planner.choose(points, goal, self._v_max, self._w_spin)
        self._nav.goal_deg = round(math.degrees(goal))

        if plan.v >= PROGRESS_FRACTION * self._v_max:
            self._progress_at = now
        elif now - self._progress_at > self._stuck_s:
            self._nav.stuck = True
            self._motion.stop("auto_stuck")
            return

        left, right = self._model.to_lr(plan.v, plan.w)
        peak = max(abs(left), abs(right))
        if peak > self._max_pwm:
            left, right = round(left * self._max_pwm / peak), round(right * self._max_pwm / peak)
        self._last_w = self._model.to_vw(left, right)[1]
        self._motion.drive(left, right, owner=f"auto:{self._nav.auto}", mode="auto")

    async def run(self) -> None:
        while True:
            self.tick()
            await asyncio.sleep(TICK_S)
