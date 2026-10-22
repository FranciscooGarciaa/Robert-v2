"""Swept-footprint collision checks, shared by the guard and the planner.

A motion is a set of K candidate paths, each sampled as J poses. Every
obstacle point is moved into each pose's frame and tested against the
safety rectangle (body + margin). Everything is one numpy broadcast of
shape (K, J, N), a few hundred thousand elements at most.

Points already inside the margin at the start (robot parked near a wall)
only count when the motion pushes them deeper, so the robot can always back
or turn away from something it is close to, never further into it.
"""

import numpy as np

STEP_M = 0.05
STEP_RAD = np.deg2rad(5)
_STRAIGHT = 1e-6
_DEEPER = 0.01  # metres a point must penetrate further to count as a hit


def arc_poses(kappa: np.ndarray, sign: np.ndarray, horizon_m: float, step: float = STEP_M):
    """Poses after travelling s = step..horizon along arcs of curvature kappa (1/m).

    sign = +1 forward, -1 reverse. Returns (s, x, y, th), each shape (K, J).
    """
    kappa = np.asarray(kappa, dtype=float)[:, None]
    s = np.arange(1, int(np.ceil(horizon_m / step)) + 1) * step
    S = np.asarray(sign, dtype=float)[:, None] * s[None, :]
    th = kappa * S
    straight = np.abs(kappa) < _STRAIGHT
    k = np.where(straight, 1.0, kappa)
    x = np.where(straight, S, np.sin(th) / k)
    y = np.where(straight, 0.0, (1 - np.cos(th)) / k)
    return np.broadcast_to(s, S.shape), x, y, th


def spin_poses(direction: np.ndarray, horizon_rad: float):
    """Poses turning in place; direction = +1 counter-clockwise (left), -1 right."""
    a = np.arange(1, int(np.ceil(horizon_rad / STEP_RAD)) + 1) * STEP_RAD
    th = np.asarray(direction, dtype=float)[:, None] * a[None, :]
    zeros = np.zeros_like(th)
    return np.broadcast_to(a, th.shape), zeros, zeros, th


def free_travel(points: np.ndarray, poses, half: tuple[float, float]) -> np.ndarray:
    """How far each path can go (metres or radians, as `poses` measures) before a hit."""
    s, x, y, th = poses
    K, J = s.shape
    if points.shape[1] == 0:
        return s[:, -1].copy()
    hl, hw = half
    px, py = points
    # Only points the footprint can reach within the horizon matter.
    reach = np.hypot(x, y).max() + np.hypot(hl, hw) + STEP_M
    near = np.hypot(px, py) <= reach
    px, py = px[near], py[near]
    if px.size == 0:
        return s[:, -1].copy()

    # A hit is a point deeper inside the rectangle than it started:
    # min(hl - |lx|, hw - |ly|) > start  <=>  |lx| < hl - start and |ly| < hw - start.
    start = np.maximum(np.minimum(hl - np.abs(px), hw - np.abs(py)), 0.0) + _DEEPER
    lim_x = (hl - start).astype(np.float32)
    lim_y = (hw - start).astype(np.float32)
    px, py = px.astype(np.float32), py.astype(np.float32)
    x, y = x[..., None].astype(np.float32), y[..., None].astype(np.float32)
    c = np.cos(th)[..., None].astype(np.float32)
    sn = np.sin(th)[..., None].astype(np.float32)
    dx = px - x
    dy = py - y
    hit = (np.abs(c * dx + sn * dy) < lim_x) & (np.abs(c * dy - sn * dx) < lim_y)
    hit = hit.any(axis=2)  # (K, J)
    first = np.where(hit.any(axis=1), hit.argmax(axis=1), J)
    # Free travel = the last collision-free sample (0 if the first one hits).
    rows = np.arange(K)
    return np.where(first > 0, s[rows, np.maximum(first - 1, 0)], 0.0)
