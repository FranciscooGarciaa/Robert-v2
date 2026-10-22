"""Explore behaviour: picks where to go next from the open space around the robot.

For 36 bearings it measures how far a robot-wide corridor runs before
hitting something, then prefers long corridors, little turning and the
direction it already chose (hysteresis), so it does not dither between
two similar gaps. Without odometry, the previous choice is carried over
by subtracting the rotation the robot was commanded to make.
"""

import math

import numpy as np

from .model import RobotModel

BEARINGS = np.deg2rad(np.arange(-180, 180, 10))
_COS, _SIN = np.cos(BEARINGS)[:, None], np.sin(BEARINGS)[:, None]
CAP_M = 3.0
W_TURN = 0.6          # metres of corridor worth giving up per pi rad of turning
KEEP_BONUS_M = 0.6    # bonus for staying near the previous choice
KEEP_WINDOW = math.radians(25)


class Explorer:
    def __init__(self, model: RobotModel):
        self.model = model
        self.goal: float | None = None

    def reset(self) -> None:
        self.goal = None

    def corridors(self, points: np.ndarray) -> np.ndarray:
        """Free corridor length (m, capped) along each of BEARINGS."""
        hl, hw = self.model.half_safe
        if points.shape[1] == 0:
            return np.full(BEARINGS.size, CAP_M)
        x, y = points
        along = _COS * x + _SIN * y       # (B, N) distance along each bearing
        across = _COS * y - _SIN * x
        blocking = (along > 0) & (np.abs(across) <= hw)
        dist = np.where(blocking, along - hl, np.inf).min(axis=1)
        return np.clip(dist, 0.0, CAP_M)

    def update(self, points: np.ndarray, turned: float) -> float:
        """New goal bearing (rad, + = left); `turned` = rotation since the last call."""
        if self.goal is not None:
            self.goal = _wrap(self.goal - turned)
        score = self.corridors(points) - W_TURN * np.abs(BEARINGS) / math.pi
        if self.goal is not None:
            near = np.abs(_wrap(BEARINGS - self.goal)) <= KEEP_WINDOW
            score = score + KEEP_BONUS_M * near
        self.goal = float(BEARINGS[int(np.argmax(score))])
        return self.goal


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi
