"""Social mode: notice someone stopping in front of the robot, so it can greet them.

The LiDAR proposes (a person-sized cluster close in front for HOLD_S while
the robot stands still and is not busy); the caller confirms with one camera
detection before anything is said. A cooldown keeps it from pestering people.
"""

import math

import numpy as np

from .follow import clusters, person_sized

NEAR_M = 1.2
FRONT = math.radians(30)
HOLD_S = 2.0
COOLDOWN_S = 60.0


class SocialGreeter:
    def __init__(self) -> None:
        self.enabled = False
        self._since: float | None = None
        self._last = -math.inf

    def someone_close(self, points: np.ndarray) -> bool:
        return any(person_sized(c) and c.range <= NEAR_M and abs(c.bearing) <= FRONT
                   for c in clusters(points))

    def update(self, points: np.ndarray | None, busy: bool, now: float) -> bool:
        """True once when a greeting should be tried (then it waits COOLDOWN_S)."""
        if not self.enabled or busy or points is None or not self.someone_close(points):
            self._since = None
            return False
        if self._since is None:
            self._since = now
        if now - self._since >= HOLD_S and now - self._last >= COOLDOWN_S:
            self._last = now
            self._since = None
            return True
        return False
