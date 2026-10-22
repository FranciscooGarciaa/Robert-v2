"""Collision guard: limits every motion command against the latest LiDAR scan.

The commanded (left, right) is turned into a path (arc or turn in place) and
swept with the safety footprint. Speed is scaled down linearly between the
"slow" and "stop" distances and cut to zero when the path is blocked. Only
the dangerous command is refused: reversing or turning away stays possible.

Blind (no recent scan): manual driving is capped, autonomous driving stops.
The ESP32 watchdog and ultrasonic veto remain underneath either way.
"""

import math

import numpy as np

from ..robot.state import NavState
from . import geometry
from .model import RobotModel
from .scan import ScanView, clearances

SPIN_RADIUS_M = 0.10       # tighter turns than this are treated as turning in place
SPIN_MARGIN_RAD = math.radians(15)
SPIN_SLOW_RAD = math.radians(30)
PATH_SAMPLES = 8


class CollisionGuard:
    def __init__(self, model: RobotModel, scan: ScanView, nav: NavState,
                 slow_extra_m: float, blind_max_speed: int, enabled: bool = True):
        self.model = model
        self.scan = scan
        self.nav = nav
        self.slow_extra_m = slow_extra_m
        self.blind_max_speed = blind_max_speed
        self.enabled = enabled

    def limit(self, left: int, right: int, autonomous: bool) -> tuple[int, int]:
        nav = self.nav
        nav.path = []
        if not self.enabled:
            nav.guard, nav.limited_by = "off", None
            return left, right
        points = self.scan.points()
        if points is None:
            nav.clearance = {}
            if left == right == 0:
                nav.guard, nav.limited_by = "blind", None
                return 0, 0
            nav.guard = "blind"
            if autonomous:
                nav.limited_by = "sin LiDAR: autónomo parado"
                return 0, 0
            cap = self.blind_max_speed
            nav.limited_by = f"sin LiDAR: máx {cap}"
            return max(-cap, min(cap, left)), max(-cap, min(cap, right))

        nav.clearance = clearances(points, self.model)
        if left == right == 0:
            nav.guard, nav.limited_by = "ok", None
            return 0, 0

        v, w = self.model.to_vw(left, right)
        if abs(v) < SPIN_RADIUS_M * abs(w):
            factor, label = self._spin(points, w)
        else:
            factor, label = self._arc(points, v, w)

        if factor <= 0:
            nav.guard, nav.limited_by = "blocked", label
            return 0, 0
        nav.guard = "ok" if factor >= 1 else "slow"
        nav.limited_by = None if factor >= 1 else label
        return round(left * factor), round(right * factor)

    def _arc(self, points: np.ndarray, v: float, w: float) -> tuple[float, str]:
        m = self.model
        stop = m.stop_distance(v)
        slow = stop + self.slow_extra_m
        poses = geometry.arc_poses(np.array([w / v]), np.array([math.copysign(1, v)]),
                                   slow + geometry.STEP_M)
        free = float(geometry.free_travel(points, poses, m.half_safe)[0])
        self.nav.path = _path(poses, free)
        label = f"{'delante' if v > 0 else 'detrás'} {free:.2f} m"
        return _ramp(free, stop, slow), label

    def _spin(self, points: np.ndarray, w: float) -> tuple[float, str]:
        m = self.model
        stop = abs(w) * m.latency_s + SPIN_MARGIN_RAD
        slow = stop + SPIN_SLOW_RAD
        poses = geometry.spin_poses(np.array([math.copysign(1, w)]), slow + geometry.STEP_RAD)
        free = float(geometry.free_travel(points, poses, m.half_safe)[0])
        return _ramp(free, stop, slow), f"giro {math.degrees(free):.0f}° libre"


def _ramp(free: float, stop: float, slow: float) -> float:
    return min(1.0, max(0.0, (free - stop) / (slow - stop)))


def _path(poses, free: float) -> list[list[float]]:
    """A few points of the collision-free part of the path, for the PWA."""
    s, x, y, _ = poses
    n = int(np.searchsorted(s[0], free, side="right"))
    if n == 0:
        return []
    idx = np.linspace(0, n - 1, min(PATH_SAMPLES, n)).round().astype(int)
    return [[round(float(x[0, i]), 3), round(float(y[0, i]), 3)] for i in idx]
