"""Robert voice service: wake word -> Gemini -> voice, as its own process.

    python -m robert.voice.service        (from pi/, reads pi/.env)

Runs apart from the backend so audio and network latency never touch motor
control. It only talks to the backend over localhost: it reports what it
heard/said, and forwards spoken commands that the backend bounds and guards.
"""

import json
import logging
import threading
import urllib.request

from .audio import Mic, Player, to_wav, tone
from .brain import Brain
from .config import VoiceSettings
from .listener import Listener
from .speaker import Speaker

log = logging.getLogger("robert.voice")

FOLLOW_UP_MS = 8000
HEARTBEAT_S = 5.0
NO_NETWORK = "Ahora mismo no tengo conexión, perdona."


class Backend:
    def __init__(self, url: str):
        self._url = url.rstrip("/")
        self._status = {"state": "idle"}
        self._lock = threading.Lock()

    def _call(self, path: str, body: dict | None = None):
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
            self._status = {"state": state, "heard": heard, "reply": reply}
        self._send()

    def _send(self) -> None:
        with self._lock:
            body = dict(self._status)
        try:
            self._call("/api/voice/event", body)
        except OSError:
            pass  # backend restarting; the heartbeat will catch up

    def heartbeat(self) -> None:
        stop = threading.Event()
        while not stop.wait(HEARTBEAT_S):
            with self._lock:
                self._status["heard"] = self._status["reply"] = None  # only refresh "state"
            self._send()

    def command(self, action: str) -> None:
        try:
            log.info("command %s -> %s", action, self._call("/api/voice/command", {"action": action}))
        except OSError as exc:
            log.warning("command %s failed: %r", action, exc)


class VoiceService:
    def __init__(self, s: VoiceSettings):
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=s.gemini_api_key,
                              http_options=types.HttpOptions(timeout=int(s.model_timeout_s * 1000)))
        self.mic = Mic(s.alsa_device)
        self.player = Player(s.alsa_device)
        self.speaker = Speaker(self.player, client, s.tts_model, s.tts_voice, s.tts_timeout_s,
                               s.piper_voice)
        self.listener = Listener(self.mic, s.wake_model, s.wake_threshold)
        self.brain = Brain(client, s.model)
        self.backend = Backend(s.backend_url)
        self.beep = tone(880, 0.12)
        self.no_network = self.speaker.piper_wav(NO_NETWORK)

    def _speak(self, wav: bytes | None = None, text: str | None = None) -> None:
        """Plays without hearing itself: the mic is muted meanwhile."""
        self.mic.mute(True)
        try:
            if wav:
                self.player.play(wav)
            elif text:
                self.speaker.say(text)
        finally:
            self.mic.mute(False)

    def conversation(self) -> None:
        """One wake: answer, then keep listening for follow-ups without the wake word."""
        self.backend.report("listening")
        pcm = self.listener.record()
        while pcm:
            self.backend.report("thinking")
            try:
                turn = self.brain.understand(to_wav(pcm), self.backend.state())
            except Exception as exc:  # noqa: BLE001 - network/API: say so and go idle
                log.warning("Gemini failed: %r", exc)
                self._speak(wav=self.no_network)
                return
            if not turn.heard:
                log.info("nothing addressed to Robert")
                return
            log.info("heard %r -> %r [%s]", turn.heard, turn.reply, turn.command)
            if turn.command != "none":
                self.backend.command(turn.command)  # before speaking: "para" acts at once
            self.backend.report("speaking", turn.heard, turn.reply)
            self._speak(text=turn.reply)
            self.backend.report("listening")
            pcm = self.listener.record(wait_for_voice_ms=FOLLOW_UP_MS)

    def run(self) -> None:
        self.mic.start()
        threading.Thread(target=self.backend.heartbeat, name="heartbeat", daemon=True).start()
        self.backend.report("idle")
        log.info("voice ready")
        while True:
            self.listener.wait_wake()
            self._speak(wav=self.beep)
            try:
                self.conversation()
            finally:
                self.backend.report("idle")


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = VoiceSettings.from_env()
    if not s.gemini_api_key:
        log.warning("GEMINI_API_KEY is empty: only the offline voice will work")
    VoiceService(s).run()


if __name__ == "__main__":
    main()
