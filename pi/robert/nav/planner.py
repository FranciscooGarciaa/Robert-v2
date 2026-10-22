"""Reactive local planner: scores a fan of arcs against the latest scan.

No map and no odometry: every tick it sweeps ~26 forward arcs (two speeds)
plus two turns in place with the safety footprint, drops the ones that
cannot stop in time, and picks the best trade-off between heading towards
the goal bearing, clearance, speed and smoothness.
"""

import math
from dataclasses import dataclass

import numpy as np

from . import geometry
from .model import RobotModel

KAPPAS = np.linspace(-3.0, 3.0, 13)   # 1/m; |kappa| = 3 -> 0.33 m radius
HORIZON_M = 2.0
LOOKAHEAD_M = 1.0
SPIN_HORIZON_RAD = math.radians(45)
SPIN_MIN_FREE_RAD = math.radians(30)
TURN_FIRST_RAD = math.radians(100)    # goal further round than this: turn in place
STOP_SLACK_M = 0.10
STEP_M = 0.10                          # coarser than the guard's 5 cm; the guard re-checks

W_GOAL, W_CLEAR, W_SPEED, W_SMOOTH = 1.0, 0.8, 0.3, 0.15


@dataclass(frozen=True)
class Plan:
    v: float          # m/s
    w: float          # rad/s, + = left
    kappa: float | None  # None when turning in place or stopped
    free_m: float


STOP = Plan(0.0, 0.0, None, 0.0)


class ArcPlanner:
    def __init__(self, model: RobotModel):
        self.model = model
        self._prev_kappa = 0.0

    def choose(self, points: np.ndarray, goal: float, v_max: float, w_spin: float) -> Plan:
        """goal: bearing in rad (robot frame, + = left); v_max m/s; w_spin rad/s."""
        if abs(goal) > TURN_FIRST_RAD and (spin := self._spin(points, goal, w_spin)):
            return spin
        m = self.model
        # The path of an arc does not depend on speed: sweep each curvature once.
        speeds = np.array([v_max, v_max / 2])
        poses = geometry.arc_poses(KAPPAS, np.ones_like(KAPPAS), HORIZON_M, STEP_M)
        free = np.tile(geometry.free_travel(points, poses, m.half_safe), speeds.size)
        kappa = np.tile(KAPPAS, speeds.size)
        v = np.repeat(speeds, KAPPAS.size)

        stop = np.array([m.stop_distance(s) for s in speeds]).repeat(KAPPAS.size)
        ok = free >= stop + STOP_SLACK_M
        if ok.any():
            look = np.minimum(free, LOOKAHEAD_M)
            th = kappa * look
            straight = np.abs(kappa) < 1e-6
            k = np.where(straight, 1.0, kappa)
            ex = np.where(straight, look, np.sin(th) / k)
            ey = np.where(straight, 0.0, (1 - np.cos(th)) / k)
            align = np.cos(np.arctan2(ey, ex) - goal)
            score = (W_GOAL * align
                     + W_CLEAR * np.minimum(free, HORIZON_M) / HORIZON_M
                     + W_SPEED * v / v_max
                     - W_SMOOTH * np.abs(kappa - self._prev_kappa) / np.ptp(KAPPAS))
            i = int(np.argmax(np.where(ok, score, -np.inf)))
            self._prev_kappa = float(kappa[i])
            return Plan(float(v[i]), float(v[i] * kappa[i]), float(kappa[i]), float(free[i]))

        # Nothing ahead can stop in time: turn in place.
        return self._spin(points, goal, w_spin) or STOP

    def _spin(self, points: np.ndarray, goal: float, w_spin: float) -> Plan | None:
        """Turn in place, towards the goal side if that side is free."""
        first = 1.0 if goal >= 0 else -1.0
        dirs = np.array([first, -first])
        free = geometry.free_travel(
            points, geometry.spin_poses(dirs, SPIN_HORIZON_RAD), self.model.half_safe)
        for d, f in zip(dirs, free):
            if f >= SPIN_MIN_FREE_RAD:
                self._prev_kappa = 0.0
                return Plan(0.0, float(d * w_spin), None, 0.0)
        return None
