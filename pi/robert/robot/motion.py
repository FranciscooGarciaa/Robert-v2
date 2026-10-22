"""Motion arbiter: the only place that decides the ESP32 target speeds.

Manual commands are dead-man style: the driver must keep re-sending them,
otherwise the target falls back to zero after `deadman_ms`.
"""

import logging
import time
from collections.abc import Callable

from .state import RobotState

log = logging.getLogger("robert.motion")


class MotionController:
    def __init__(
        self,
        state: RobotState,
        max_speed: int,
        deadman_ms: int,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._state = state
        self._max = max_speed
        self._deadman_s = deadman_ms / 1000
        self._clock = clock
        self._last_cmd = 0.0
        self._stop_listeners: list[Callable[[], None]] = []

    @property
    def max_speed(self) -> int:
        return self._max

    def on_stop(self, listener: Callable[[], None]) -> None:
        """Register a callback fired on every stop (to send it immediately)."""
        self._stop_listeners.append(listener)

    def drive(self, left: int, right: int, owner: str) -> None:
        m = self._state.motion
        left = max(-self._max, min(self._max, int(left)))
        right = max(-self._max, min(self._max, int(right)))
        if owner != m.owner:
            log.info("driver is now %s", owner)
        if (left, right) != (m.target_left, m.target_right):
            log.info("drive L=%d R=%d (%s)", left, right, owner)
        m.target_left, m.target_right = left, right
        m.mode = "manual"
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

    def release(self, owner: str) -> None:
        """A client went away; stop if it was the one driving."""
        if self._state.motion.owner == owner:
            self.stop("driver_disconnected")

    def current_target(self) -> tuple[int, int]:
        m = self._state.motion
        if m.mode == "manual" and self._clock() - self._last_cmd > self._deadman_s:
            self.stop("deadman")
        return m.target_left, m.target_right
