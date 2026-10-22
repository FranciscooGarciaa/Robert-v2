"""Synthetic LiDAR scans for nav tests: ray-cast boxes in the robot frame."""

import math

from robert.nav.model import RobotModel
from robert.nav.scan import ScanView
from robert.robot.state import LidarState

Box = tuple[float, float, float, float]  # xmin, xmax, ymin, ymax (m; x fwd, y left)

MODEL = RobotModel(length_m=0.35, width_m=0.30, margin_m=0.10, mps_per_pwm=0.004,
                   track_eff_m=0.40, latency_s=0.25, decel_mps2=1.0)


def bins_for(boxes: list[Box], max_m: float = 12.0) -> list[int]:
    bins = []
    for i in range(360):
        th = -math.radians(i + 0.5)  # bins are clockwise; REP 103 is counter-clockwise
        dx, dy = math.cos(th), math.sin(th)
        best = math.inf
        for x0, x1, y0, y1 in boxes:
            t_in, t_out = 0.0, math.inf
            for o, d, lo, hi in ((0.0, dx, x0, x1), (0.0, dy, y0, y1)):
                if abs(d) < 1e-12:
                    if not lo <= o <= hi:
                        t_in, t_out = 1, 0
                    continue
                a, b = sorted(((lo - o) / d, (hi - o) / d))
                t_in, t_out = max(t_in, a), min(t_out, b)
            if t_in <= t_out:
                best = min(best, t_in)
        bins.append(round(best * 1000) if best <= max_m else 0)
    return bins


class FakeClock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def scan_view(boxes: list[Box], clock: FakeClock | None = None,
              model: RobotModel = MODEL) -> tuple[ScanView, LidarState, FakeClock]:
    clock = clock or FakeClock()
    lidar = LidarState(connected=True, bins=bins_for(boxes), last_scan=clock.t)
    return ScanView(lidar, model, stale_s=0.35, clock=clock), lidar, clock


def wall(x: float) -> Box:
    """A wide wall across the path at distance x ahead (negative = behind)."""
    return (x, x + 0.05, -3, 3) if x > 0 else (x - 0.05, x, -3, 3)
