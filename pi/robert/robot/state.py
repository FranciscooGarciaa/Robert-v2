"""Central runtime state. Components write here; the API only reads it."""

import time
from dataclasses import asdict, dataclass, field


@dataclass
class Esp32State:
    connected: bool = False
    last_telemetry: float | None = None  # time.monotonic()
    reconnects: int = 0
    reset_reason: str | None = None
    failsafe: str | None = None
    wifi_rssi: int | None = None
    uptime_ms: int | None = None
    left_target: int = 0   # what the ESP32 says it was asked for
    right_target: int = 0
    left_out: int = 0      # what the ESP32 is actually driving
    right_out: int = 0


@dataclass
class UltrasonicState:
    distance_cm: float | None = None
    obstacle: bool = False
    timeouts: int = 0


@dataclass
class MotionState:
    target_left: int = 0
    target_right: int = 0
    mode: str = "idle"           # idle | manual
    owner: str | None = None     # PWA client currently driving
    last_stop_reason: str | None = "startup"


@dataclass
class RobotState:
    esp32: Esp32State = field(default_factory=Esp32State)
    ultrasonic: UltrasonicState = field(default_factory=UltrasonicState)
    motion: MotionState = field(default_factory=MotionState)
    started_at: float = field(default_factory=time.monotonic)

    def snapshot(self) -> dict:
        now = time.monotonic()
        data = asdict(self)
        last = data["esp32"].pop("last_telemetry")
        data["esp32"]["telemetry_age_ms"] = None if last is None else int((now - last) * 1000)
        data.pop("started_at")
        data["system"] = {"uptime_s": int(now - self.started_at)}
        return data
