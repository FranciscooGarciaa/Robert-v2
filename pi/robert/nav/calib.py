"""LiDAR mounting calibration from a few scans (used by tools/calibrate_lidar.py).

All bins are in the robot frame the backend publishes: bin i = i..i+1
degrees clockwise from the front, millimetres, 0 = no return. Robot frame =
(sensor angle, mirrored unless clockwise) + offset (see lidar.ScanAssembler).
"""

import json
from pathlib import Path

import numpy as np

LEFT_DEG = 270          # clockwise from the front
SELF_MAX_MM = 450       # the robot's own parts are closer than this...
SELF_TOLERANCE_MM = 30  # ...and stay put while the room moves around it
BIG_BLIND_DEG = 60
CLUSTER_MM = 15


def nearest_bin(bins: list[int], max_mm: int = 1000, ignore: np.ndarray | None = None) -> int:
    """Direction (bin) of the closest object under max_mm, skipping masked bins.

    A flat face gives several near-equal bins; the centre of the bins within
    CLUSTER_MM of the minimum is a steadier estimate than the single minimum.
    """
    mm = np.asarray(bins, dtype=float)
    ok = (mm > 0) & (mm < max_mm)
    if ignore is not None:
        ok &= ~ignore
    if not ok.any():
        raise ValueError("no object closer than %d mm" % max_mm)
    near = ok & (mm <= mm[ok].min() + CLUSTER_MM)
    angles = np.deg2rad(np.flatnonzero(near) + 0.5)
    mean = np.degrees(np.arctan2(np.sin(angles).mean(), np.cos(angles).mean())) - 0.5
    return int(round(mean)) % 360


def offset_fix(front_bin: int, offset_deg: float) -> float:
    """New offset so that an object straight ahead lands on bin 0."""
    return (offset_deg - front_bin) % 360


def direction_fix(left_bin: int, offset_deg: float, clockwise: bool) -> tuple[float, bool]:
    """(offset, clockwise) after checking an object on the robot's left side.

    With the front already fixed, a mirrored sensor shows the left object on
    the right (~90). Mirroring flips the sign of the angle, so the offset
    flips too to keep the front on bin 0.
    """
    if abs(_wrap(left_bin - LEFT_DEG)) <= 45:
        return offset_deg % 360, clockwise
    if abs(_wrap(left_bin - (360 - LEFT_DEG))) <= 45:
        return (-offset_deg) % 360, not clockwise
    raise ValueError(f"left object seen at {left_bin} deg: neither side, move it and retry")


def self_mask(scans: list[list[int]]) -> np.ndarray:
    """Bins where every scan, taken with the robot moved between them, sees
    the same close return: that is the robot itself (or something on it)."""
    mm = np.asarray(scans, dtype=float)
    close = (mm > 0) & (mm < SELF_MAX_MM)
    steady = (mm.max(axis=0) - mm.min(axis=0)) <= SELF_TOLERANCE_MM
    return close.all(axis=0) & steady


def sectors(mask: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous masked runs as (first, last) bins, wrapping around 360."""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    if idx.size == mask.size:
        return [(0, mask.size - 1)]
    runs, start, prev = [], int(idx[0]), int(idx[0])
    for i in idx[1:]:
        if i != prev + 1:
            runs.append((start, prev))
            start = int(i)
        prev = int(i)
    runs.append((start, prev))
    if len(runs) > 1 and runs[0][0] == 0 and runs[-1][1] == mask.size - 1:
        runs[0] = (runs[-1][0], runs[0][1])  # joins across 359 -> 0
        runs.pop()
    return runs


def save_mask(mask: np.ndarray, path: Path, offset_deg: float, clockwise: bool) -> None:
    """Stored in the sensor's own angles, so it survives a later change of
    LIDAR_OFFSET_DEG / LIDAR_CLOCKWISE (the shell does not move on the sensor)."""
    sign = 1 if clockwise else -1
    robot = np.flatnonzero(mask)
    sensor = np.sort((sign * (robot - offset_deg)).round().astype(int) % 360)
    path.write_text(json.dumps({"sensor_bins": sensor.tolist(),
                                "robot_sectors_when_saved": sectors(mask)}, indent=1) + "\n")


def load_mask(path: Path | None, offset_deg: float = 0.0, clockwise: bool = True) -> np.ndarray:
    mask = np.zeros(360, dtype=bool)
    if path and path.is_file():
        sensor = np.asarray(json.loads(path.read_text())["sensor_bins"], dtype=int)
        sign = 1 if clockwise else -1
        mask[(sign * sensor + round(offset_deg)) % 360] = True
    return mask


def _wrap(deg: float) -> float:
    return (deg + 180) % 360 - 180
