"""Runtime configuration from environment variables (and an optional .env)."""

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # pi/


def _load_dotenv(path: Path) -> None:
    """Minimal KEY=VALUE loader; real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    esp32_host: str = "robert-motor.local"
    esp32_port: int = 81
    esp32_token: str = ""
    esp32_send_hz: float = 10.0          # move/heartbeat rate to the ESP32
    esp32_telemetry_timeout_s: float = 1.5  # no telemetry -> connection dead
    max_speed: int = 180                 # cap applied to every manual command
    deadman_ms: int = 300                # PWA must refresh drive within this
    state_push_hz: float = 10.0          # state broadcast rate to PWA clients
    lidar_enabled: bool = True
    lidar_port: str = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"  # CH340
    lidar_baud: int = 230400
    lidar_offset_deg: float = 0.0        # added after mapping to the robot frame
    lidar_clockwise: bool = True         # sensor angles grow clockwise from above
    lidar_push_hz: float = 5.0           # scan broadcast rate to PWA clients
    # Robot geometry and motion model (measure; see tools/calibrate_motion.py).
    # Until calibrated, MPS_PER_PWM over-estimates speed so braking starts early.
    robot_length_m: float = 0.35
    robot_width_m: float = 0.30
    robot_margin_m: float = 0.10         # clearance kept around the body
    lidar_x_m: float = 0.0               # LiDAR position from the robot centre
    lidar_y_m: float = 0.0               # (x forward, y left)
    mps_per_pwm: float = 0.004
    track_eff_m: float = 0.40            # effective skid-steer track width
    pwm_deadband: int = 40               # below this the wheels do not turn
    # Collision guard (applies to every motion command).
    guard_enabled: bool = True
    guard_latency_s: float = 0.25        # scan age + command transport
    guard_decel_mps2: float = 1.0
    guard_slow_extra_m: float = 0.40     # start slowing this far before stopping
    guard_stale_s: float = 0.35          # older scans count as "blind"
    guard_blind_max_speed: int = 80      # manual cap while blind (ESP32 HC-SR04 still vetoes)
    # Autonomy.
    auto_max_speed: int = 100
    auto_spin_speed: int = 90
    auto_arm_timeout_ms: int = 300       # PWA must keep re-arming within this
    auto_stuck_s: float = 8.0
    http_host: str = "0.0.0.0"
    http_port: int = 8000
    frontend_dir: Path = ROOT.parent / "frontend"

    @classmethod
    def from_env(cls, dotenv: Path | None = ROOT / ".env") -> "Settings":
        if dotenv:
            _load_dotenv(dotenv)
        e = os.environ
        return cls(
            esp32_host=e.get("ESP32_HOST", cls.esp32_host),
            esp32_port=int(e.get("ESP32_PORT", cls.esp32_port)),
            esp32_token=e.get("ESP32_TOKEN", cls.esp32_token),
            esp32_send_hz=float(e.get("ESP32_SEND_HZ", cls.esp32_send_hz)),
            esp32_telemetry_timeout_s=float(
                e.get("ESP32_TELEMETRY_TIMEOUT_S", cls.esp32_telemetry_timeout_s)
            ),
            max_speed=max(0, min(255, int(e.get("MAX_SPEED", cls.max_speed)))),
            deadman_ms=int(e.get("DEADMAN_MS", cls.deadman_ms)),
            state_push_hz=float(e.get("STATE_PUSH_HZ", cls.state_push_hz)),
            lidar_enabled=_bool(e.get("LIDAR_ENABLED"), cls.lidar_enabled),
            lidar_port=e.get("LIDAR_PORT", cls.lidar_port),
            lidar_baud=int(e.get("LIDAR_BAUD", cls.lidar_baud)),
            lidar_offset_deg=float(e.get("LIDAR_OFFSET_DEG", cls.lidar_offset_deg)),
            lidar_clockwise=_bool(e.get("LIDAR_CLOCKWISE"), cls.lidar_clockwise),
            lidar_push_hz=float(e.get("LIDAR_PUSH_HZ", cls.lidar_push_hz)),
            robot_length_m=float(e.get("ROBOT_LENGTH_M", cls.robot_length_m)),
            robot_width_m=float(e.get("ROBOT_WIDTH_M", cls.robot_width_m)),
            robot_margin_m=float(e.get("ROBOT_MARGIN_M", cls.robot_margin_m)),
            lidar_x_m=float(e.get("LIDAR_X_M", cls.lidar_x_m)),
            lidar_y_m=float(e.get("LIDAR_Y_M", cls.lidar_y_m)),
            mps_per_pwm=float(e.get("MPS_PER_PWM", cls.mps_per_pwm)),
            track_eff_m=float(e.get("TRACK_EFF_M", cls.track_eff_m)),
            pwm_deadband=int(e.get("PWM_DEADBAND", cls.pwm_deadband)),
            guard_enabled=_bool(e.get("GUARD_ENABLED"), cls.guard_enabled),
            guard_latency_s=float(e.get("GUARD_LATENCY_S", cls.guard_latency_s)),
            guard_decel_mps2=float(e.get("GUARD_DECEL_MPS2", cls.guard_decel_mps2)),
            guard_slow_extra_m=float(e.get("GUARD_SLOW_EXTRA_M", cls.guard_slow_extra_m)),
            guard_stale_s=float(e.get("GUARD_STALE_S", cls.guard_stale_s)),
            guard_blind_max_speed=int(e.get("GUARD_BLIND_MAX_SPEED", cls.guard_blind_max_speed)),
            auto_max_speed=int(e.get("AUTO_MAX_SPEED", cls.auto_max_speed)),
            auto_spin_speed=int(e.get("AUTO_SPIN_SPEED", cls.auto_spin_speed)),
            auto_arm_timeout_ms=int(e.get("AUTO_ARM_TIMEOUT_MS", cls.auto_arm_timeout_ms)),
            auto_stuck_s=float(e.get("AUTO_STUCK_S", cls.auto_stuck_s)),
            http_host=e.get("HTTP_HOST", cls.http_host),
            http_port=int(e.get("HTTP_PORT", cls.http_port)),
            frontend_dir=Path(e.get("FRONTEND_DIR", cls.frontend_dir)),
        )
