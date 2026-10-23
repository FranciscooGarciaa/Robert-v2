"""Motion arbiter: the only place that decides the ESP32 target speeds.

Every command (manual or autonomous) is dead-man style: the driver must keep
re-sending it, otherwise the target falls back to zero after `deadman_ms`.
The optional collision guard then limits what is actually sent.
"""

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from .state import RobotState

if TYPE_CHECKING:
    from ..nav.guard import CollisionGuard

log = logging.getLogger("robert.motion")


class MotionController:
    def __init__(
        self,
        state: RobotState,
        max_speed: int,
        deadman_ms: int,
        clock: Callable[[], float] = time.monotonic,
        guard: "CollisionGuard | None" = None,
    ):
        self._state = state
        self._max = max_speed
        self._deadman_s = deadman_ms / 1000
        self._clock = clock
        self._guard = guard
        self._last_cmd = 0.0
        self._stop_listeners: list[Callable[[], None]] = []

    @property
    def max_speed(self) -> int:
        return self._max

    def on_stop(self, listener: Callable[[], None]) -> None:
        """Register a callback fired on every stop (to send it immediately)."""
        self._stop_listeners.append(listener)

    def drive(self, left: int, right: int, owner: str, mode: str = "manual") -> None:
        m = self._state.motion
        left = max(-self._max, min(self._max, int(left)))
        right = max(-self._max, min(self._max, int(right)))
        if owner != m.owner:
            log.info("driver is now %s", owner)
        if (left, right) != (m.target_left, m.target_right):
            # Autonomy changes the command every tick; keep it out of INFO.
            log.log(logging.INFO if mode == "manual" else logging.DEBUG,
                    "drive L=%d R=%d (%s)", left, right, owner)
        m.target_left, m.target_right = left, right
        m.mode = mode
        m.owner = owner
        self._last_cmd = self._clock()

    def stop(self, reason: str) -> None:
        m = self._state.motion
        m.target_left = m.target_right = 0
        m.mode = "idle"
        m.owner = None
        m.last_stop_reason = reason
        log.info("STOP (%s)", reason)
        for listener in self._stop_listeners:
            listener()

    def release(self, owner: str, reason: str = "driver_disconnected") -> None:
        """A driver is done (or went away); stop if it is still the one driving."""
        if self._state.motion.owner == owner:
            self.stop(reason)

    def current_target(self) -> tuple[int, int]:
        """What to send to the ESP32 now (called every send tick)."""
        m = self._state.motion
        if m.mode != "idle" and self._clock() - self._last_cmd > self._deadman_s:
            self.stop("deadman")
        left, right = m.target_left, m.target_right
        if self._guard:
            left, right = self._guard.limit(left, right, autonomous=m.mode == "auto")
        nav = self._state.nav
        nav.out_left, nav.out_right = left, right
        return left, right
