"""Localhost client for the Robert backend (reports + spoken commands)."""

import json
import logging
import threading
import time
import urllib.request

log = logging.getLogger("robert.voice.backend")

HEARTBEAT_S = 5.0


class Backend:
    def __init__(self, url: str):
        self._url = url.rstrip("/")
        self._state = "idle"
        self._lock = threading.Lock()

    def _call(self, path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self._url + path, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.load(r)

    def state(self) -> dict | None:
        try:
            return self._call("/api/state")
        except OSError:
            return None

    def report(self, state: str, heard: str | None = None, reply: str | None = None) -> None:
        with self._lock:
            self._state = state
        try:
            self._call("/api/voice/event", {"state": state, "heard": heard, "reply": reply})
        except OSError:
            pass  # backend restarting; the heartbeat catches up

    def heartbeat(self) -> None:
        """Keeps the backend from showing the voice as offline between events."""
        while True:
            time.sleep(HEARTBEAT_S)
            with self._lock:
                state = self._state
            try:
                self._call("/api/voice/event", {"state": state})
            except OSError:
                pass

    def command(self, action: str, style: str | None = None) -> dict:
        body = {"action": action} if style is None else {"action": action, "style": style}
        try:
            return self._call("/api/voice/command", body)
        except OSError as exc:
            log.warning("command %s failed: %r", action, exc)
            return {"ok": False, "reason": "el controlador no responde"}
