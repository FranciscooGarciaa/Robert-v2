"""Latest LiDAR revolution as robot-frame points (numpy), computed once per scan."""

import time
from collections.abc import Callable

import numpy as np

from ..robot.state import LidarState
from .model import RobotModel

# Bin i covers i..i+1 degrees clockwise from the front; REP 103 is counter-clockwise.
_ANGLES = -np.deg2rad(np.arange(360) + 0.5)
_COS, _SIN = np.cos(_ANGLES), np.sin(_ANGLES)


class ScanView:
    """`mask` marks bins the LiDAR cannot see through (the robot's own parts,
    from tools/calibrate_lidar.py). Their returns are dropped, and callers
    can ask how blind a direction is before moving that way."""

    def __init__(self, lidar: LidarState, model: RobotModel, stale_s: float,
                 clock: Callable[[], float] = time.monotonic, mask: np.ndarray | None = None):
        self._lidar = lidar
        self.mask = np.zeros(360, dtype=bool) if mask is None else mask
        self._model = model
        self._stale_s = stale_s
        self._clock = clock
        self._bins = None
        self._points = np.empty((2, 0))

    def blind_deg(self, center_deg: float, half_width_deg: float = 60) -> int:
        """Masked degrees within +-half_width of a direction (clockwise from the front)."""
        offsets = (np.arange(360) - center_deg + 180) % 360 - 180
        return int((self.mask & (np.abs(offsets) <= half_width_deg)).sum())

    def age_s(self) -> float | None:
        last = self._lidar.last_scan
        return None if last is None else self._clock() - last

    def points(self) -> np.ndarray | None:
        """(2, N) array of x, y in metres around the robot centre, or None if blind."""
        bins = self._lidar.bins
        age = self.age_s()
        if bins is None or age is None or age > self._stale_s:
            return None
        if bins is not self._bins:
            self._bins = bins
            self._points = self._convert(np.asarray(bins, dtype=float))
        return self._points

    def _convert(self, mm: np.ndarray) -> np.ndarray:
        m = self._model
        valid = (mm > 0) & ~self.mask
        r = mm[valid] / 1000
        x = r * _COS[valid] + m.lidar_x_m
        y = r * _SIN[valid] + m.lidar_y_m
        # Returns inside the body are the robot itself (mast, cables): drop them.
        hl, hw = m.half_body
        outside = (np.abs(x) > hl) | (np.abs(y) > hw)
        return np.vstack((x[outside], y[outside]))


def clearances(points: np.ndarray, model: RobotModel) -> dict[str, float | None]:
    """Gap between the body and the nearest return straight ahead/behind/left/right."""
    hl, hw = model.half_body
    x, y = points
    side = np.abs(y) <= hw
    along = np.abs(x) <= hl

    def gap(mask: np.ndarray, values: np.ndarray) -> float | None:
        return round(float(values[mask].min()), 2) if mask.any() else None

    return {
        "front": gap(side & (x > 0), x - hl),
        "back": gap(side & (x < 0), -x - hl),
        "left": gap(along & (y > 0), y - hw),
        "right": gap(along & (y < 0), -y - hw),
    }
