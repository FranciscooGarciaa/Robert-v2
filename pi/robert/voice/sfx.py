"""Little robot noises (R2-D2 style), synthesised on the fly: no sound files."""

import numpy as np

from .audio import OUT_RATE

_rng = np.random.default_rng()


def _sweep(f0: float, f1: float, seconds: float, level: float = 0.22) -> np.ndarray:
    n = int(seconds * OUT_RATE)
    freq = np.geomspace(f0, f1, n)
    phase = 2 * np.pi * np.cumsum(freq) / OUT_RATE
    ramp = np.minimum(1, np.minimum(np.arange(n), n - np.arange(n)) / (0.008 * OUT_RATE))
    return level * ramp * np.sin(phase)


def _pcm(x: np.ndarray) -> bytes:
    return (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()


def _gap(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * OUT_RATE))


def chirps(n: int = 5) -> bytes:
    """A happy burble of random beeps and sweeps."""
    parts = []
    for _ in range(n):
        f0, f1 = _rng.uniform(700, 2600, 2)
        parts += [_sweep(f0, f1, _rng.uniform(0.05, 0.14)), _gap(_rng.uniform(0.01, 0.05))]
    return _pcm(np.concatenate(parts))


def sad() -> bytes:
    """Descending 'oh no' (lost you)."""
    return _pcm(np.concatenate([_sweep(1400, 900, 0.25), _gap(0.05), _sweep(900, 400, 0.45)]))


def countdown() -> bytes:
    """Three beeps a second apart and a higher one: 3, 2, 1, now."""
    beep = _sweep(1000, 1000, 0.12)
    parts = []
    for _ in range(3):
        parts += [beep, _gap(0.88)]
    parts.append(_sweep(1800, 1800, 0.25))
    return _pcm(np.concatenate(parts))


def shutter() -> bytes:
    noise = _rng.uniform(-0.3, 0.3, int(0.05 * OUT_RATE)) * np.linspace(1, 0, int(0.05 * OUT_RATE))
    return _pcm(np.concatenate([noise, _gap(0.04), noise * 0.6]))
