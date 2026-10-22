"""Robot geometry and the PWM <-> velocity model of the skid-steer base.

Frames follow ROS REP 103: x forward, y left, angles counter-clockwise,
metres and radians. There are no wheel encoders, so velocities are an
open-loop estimate from PWM (calibrated with tools/calibrate_motion.py).
"""

from dataclasses import dataclass

from ..config import Settings


@dataclass(frozen=True)
class RobotModel:
    length_m: float = 0.325
    width_m: float = 0.16
    margin_m: float = 0.10
    lidar_x_m: float = 0.0
    lidar_y_m: float = 0.0
    mps_per_pwm: float = 0.004
    track_eff_m: float = 0.40
    pwm_deadband: int = 40
    latency_s: float = 0.25
    decel_mps2: float = 1.0

    @classmethod
    def from_settings(cls, s: Settings) -> "RobotModel":
        return cls(
            s.robot_length_m, s.robot_width_m, s.robot_margin_m, s.lidar_x_m, s.lidar_y_m,
            s.mps_per_pwm, s.track_eff_m, s.pwm_deadband, s.guard_latency_s, s.guard_decel_mps2,
        )

    @property
    def half_body(self) -> tuple[float, float]:
        return self.length_m / 2, self.width_m / 2

    @property
    def half_safe(self) -> tuple[float, float]:
        """Body inflated by the margin: what must never touch an obstacle."""
        return self.length_m / 2 + self.margin_m, self.width_m / 2 + self.margin_m

    def to_vw(self, left: int, right: int) -> tuple[float, float]:
        v = (left + right) / 2 * self.mps_per_pwm
        w = (right - left) * self.mps_per_pwm / self.track_eff_m
        return v, w

    def to_lr(self, v: float, w: float) -> tuple[int, int]:
        mid = v / self.mps_per_pwm
        diff = w * self.track_eff_m / (2 * self.mps_per_pwm)
        return round(mid - diff), round(mid + diff)

    def stop_distance(self, speed: float) -> float:
        """Path travelled before standing still when braking now."""
        speed = abs(speed)
        return speed * self.latency_s + speed * speed / (2 * self.decel_mps2)
