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
            http_host=e.get("HTTP_HOST", cls.http_host),
            http_port=int(e.get("HTTP_PORT", cls.http_port)),
            frontend_dir=Path(e.get("FRONTEND_DIR", cls.frontend_dir)),
        )
