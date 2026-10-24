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
class LidarState:
    enabled: bool = False
    connected: bool = False
    scans: int = 0
    scan_hz: float | None = None       # measured on the Pi
    reported_hz: float | None = None   # what the sensor reports
    points_per_scan: int = 0
    nearest_mm: int | None = None
    nearest_deg: int | None = None     # robot frame, 0 = front, clockwise
    checksum_errors: int = 0
    last_scan: float | None = None     # time.monotonic()
    bins: list[int] | None = None      # latest revolution, pushed separately


@dataclass
class NavState:
    guard: str = "off"                 # ok | slow | blocked | blind | off
    limited_by: str | None = None      # why the guard is slowing/stopping
    out_left: int = 0                  # command after the guard (sent to the ESP32)
    out_right: int = 0
    path: list[list[float]] = field(default_factory=list)  # [x, y] m, x fwd, y left
    clearance: dict[str, float | None] = field(default_factory=dict)
    auto: str | None = None            # explore | None
    goal_deg: float | None = None      # explore target bearing, + = left
    stuck: bool = False
    footprint: list[float] = field(default_factory=list)   # [length, width, margin] m
    blind: list[list[int]] = field(default_factory=list)   # masked [first, last] bins
    follow: dict = field(default_factory=dict)              # state, distance_m, bearing_deg


@dataclass
class VisionState:
    people: list[dict] = field(default_factory=list)  # [{bearing_deg, half_width_deg, score}]
    updated: float | None = None                      # time.monotonic() of the last report


@dataclass
class VoiceState:
    state: str = "offline"             # offline | idle | listening | thinking | speaking
    heard: str | None = None           # last thing Robert understood
    reply: str | None = None           # last thing Robert said
    updated: float | None = None       # time.monotonic() of the last report


@dataclass
class MotionState:
    target_left: int = 0
    target_right: int = 0
    mode: str = "idle"           # idle | manual | auto
    owner: str | None = None     # PWA client currently driving
    last_stop_reason: str | None = "startup"


@dataclass
class RobotState:
    esp32: Esp32State = field(default_factory=Esp32State)
    ultrasonic: UltrasonicState = field(default_factory=UltrasonicState)
    motion: MotionState = field(default_factory=MotionState)
    lidar: LidarState = field(default_factory=LidarState)
    nav: NavState = field(default_factory=NavState)
    voice: VoiceState = field(default_factory=VoiceState)
    vision: VisionState = field(default_factory=VisionState)
    started_at: float = field(default_factory=time.monotonic)

    def snapshot(self) -> dict:
        now = time.monotonic()
        data = asdict(self)
        last = data["esp32"].pop("last_telemetry")
        data["esp32"]["telemetry_age_ms"] = None if last is None else int((now - last) * 1000)
        data.pop("started_at")
        lidar = data["lidar"]
        lidar.pop("bins")
        last = lidar.pop("last_scan")
        lidar["scan_age_ms"] = None if last is None else int((now - last) * 1000)
        vision = data["vision"]
        last = vision.pop("updated")
        vision["age_ms"] = None if last is None else int((now - last) * 1000)
        voice = data["voice"]
        last = voice.pop("updated")
        # The voice service reports at least every few seconds; silence = it is down.
        if last is None or now - last > 15:
            voice["state"] = "offline"
        data["system"] = {"uptime_s": int(now - self.started_at)}
        return data
