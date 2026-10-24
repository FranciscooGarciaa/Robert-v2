"""Who to follow: the camera says "a person is that way", the LiDAR says exactly where.

Acquisition needs both: a LiDAR cluster of person size, close and roughly in
front, in the same direction as a camera detection. From then on the LiDAR
tracks that cluster at 10 Hz (alpha-beta filter in the robot frame, gated
association), so the person can step out of the camera's narrow view. If the
cluster is not seen for LOST_S the target is lost; a person reappearing near
the last position within REACQUIRE_S takes it back, otherwise it is gone.

No odometry: positions are robot-relative and the filter's velocity absorbs
the robot's own motion, which is slow next to the 10 Hz update.
"""

import math
from dataclasses import dataclass

import numpy as np

CLUSTER_GAP_M = 0.15
PERSON_WIDTH_M = (0.03, 0.8)     # one leg seen from 3 m (2-3 points) ... two legs / torso
ACQUIRE_RANGE_M = 3.0
ACQUIRE_BEARING = math.radians(25)
ACQUIRE_S = 3.0
GATE_M = 0.5
REACQUIRE_GATE_M = 0.7
LOST_S = 2.0
REACQUIRE_S = 4.0
CAMERA_MATCH = math.radians(6)   # slack around a detection's angular width
ALPHA, BETA = 0.6, 0.2


@dataclass(frozen=True)
class Cluster:
    x: float
    y: float
    width: float

    @property
    def range(self) -> float:
        return math.hypot(self.x, self.y)

    @property
    def bearing(self) -> float:   # rad, + = left
        return math.atan2(self.y, self.x)


@dataclass(frozen=True)
class Person:
    """A camera detection: direction and angular half-width (rad, + = left)."""
    bearing: float
    half_width: float


def clusters(points: np.ndarray) -> list[Cluster]:
    """Splits the scan (ordered by angle) where consecutive points jump > CLUSTER_GAP_M."""
    if points.shape[1] == 0:
        return []
    x, y = points
    order = np.argsort(np.arctan2(y, x))
    x, y = x[order], y[order]
    jumps = np.hypot(np.diff(x), np.diff(y)) > CLUSTER_GAP_M
    bounds = np.flatnonzero(jumps) + 1
    out = []
    for idx in np.split(np.arange(x.size), bounds):
        if idx.size < 2:
            continue
        cx, cy = x[idx], y[idx]
        width = math.hypot(cx[-1] - cx[0], cy[-1] - cy[0])
        out.append(Cluster(float(cx.mean()), float(cy.mean()), width))
    return out


def person_sized(c: Cluster) -> bool:
    return PERSON_WIDTH_M[0] <= c.width <= PERSON_WIDTH_M[1]


def seen_by_camera(c: Cluster, people: list[Person]) -> bool:
    return any(abs(_wrap(c.bearing - p.bearing)) <= p.half_width + CAMERA_MATCH for p in people)


class Follower:
    """States: idle, searching, following, lost, gone (gave up), failed (never found)."""

    def __init__(self) -> None:
        self.state = "idle"
        self._pos = np.zeros(2)
        self._vel = np.zeros(2)
        self._since = self._seen = self._t = 0.0

    def start(self, now: float) -> None:
        self.state = "searching"
        self._since = self._t = now

    def stop(self) -> None:
        self.state = "idle"

    @property
    def active(self) -> bool:
        return self.state in ("searching", "following", "lost")

    @property
    def distance(self) -> float | None:
        return float(np.hypot(*self._pos)) if self.state in ("following", "lost") else None

    @property
    def bearing(self) -> float | None:
        return float(math.atan2(self._pos[1], self._pos[0])) if self.state in ("following", "lost") else None

    def update(self, points: np.ndarray, people: list[Person], now: float) -> None:
        found = [c for c in clusters(points) if person_sized(c)]
        if self.state == "searching":
            self._acquire(found, people, now)
            return
        if self.state not in ("following", "lost"):
            return
        dt = max(now - self._t, 1e-3)
        self._t = now
        predicted = self._pos + self._vel * dt
        gate = GATE_M if self.state == "following" else REACQUIRE_GATE_M
        best = min(found, key=lambda c: np.hypot(c.x - predicted[0], c.y - predicted[1]), default=None)
        if best is not None and np.hypot(best.x - predicted[0], best.y - predicted[1]) <= gate:
            residual = np.array([best.x, best.y]) - predicted
            self._pos = predicted + ALPHA * residual
            self._vel = self._vel + BETA * residual / dt
            self._seen = now
            self.state = "following"
            return
        self._pos = predicted if self.state == "following" else self._pos
        self._vel *= 0.8  # do not coast far on a guess
        if self.state == "following" and now - self._seen > LOST_S:
            self.state = "lost"
        elif self.state == "lost" and now - self._seen > LOST_S + REACQUIRE_S:
            self.state = "gone"

    def _acquire(self, found: list[Cluster], people: list[Person], now: float) -> None:
        candidates = [c for c in found
                      if c.range <= ACQUIRE_RANGE_M and abs(c.bearing) <= ACQUIRE_BEARING
                      and seen_by_camera(c, people)]
        if candidates:
            c = min(candidates, key=lambda c: c.range)
            self._pos = np.array([c.x, c.y])
            self._vel = np.zeros(2)
            self._seen = self._t = now
            self.state = "following"
        elif now - self._since > ACQUIRE_S:
            self.state = "failed"


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi
