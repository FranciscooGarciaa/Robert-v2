"""Robert voice service: wake word -> live AI conversation, as its own process.

    python -m robert.voice.service        (from pi/, reads pi/.env)

Runs apart from the backend so audio and network latency never touch motor
control. It only talks to the backend over localhost: it reports what it
heard/said, and forwards spoken commands that the backend bounds and guards.
"""

import asyncio
import logging
import socket
import threading

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import assets, camera, sfx
from .audio import Mic, StreamPlayer
from .backend import Backend
from .config import VoiceSettings
from .listener import Vad, WakeWord
from . import providers
from .live import LiveConversation, instructions, introduce, wake_phrase

log = logging.getLogger("robert.voice")

GREET_PORT = 8001
REMOTE_PORT = 8003        # raw PCM16 16 kHz from the PWA (via the backend), one connection per press
END_OF_TURN_SILENCE_S = 1.0
GREETING = ("Se acaba de parar delante de ti una persona desconocida. Salúdala con gracia, "
            "preséntate como {me} y hazle una pregunta corta.")


class VoiceService:
    def __init__(self, s: VoiceSettings):
        self.s = s
        self.ai = providers.make(s.ai_provider, s.ai_api_key, s.ai_model, s.ai_voice, s.silence_ms)
        self.mic = Mic(s.alsa_device)
        self.player = StreamPlayer(s.alsa_device)
        self.vad = Vad()
        self.wake = WakeWord(self.mic, s.wake_model, s.wake_threshold)
        self.backend = Backend(s.backend_url)
        self.beep = assets.beep()
        self.no_network = assets.phrase("no_network", s.assets_dir, s.piper_voice)
        self.no_credit = assets.phrase("no_credit", s.assets_dir, s.piper_voice)
        self.greet = threading.Event()  # interrupts the wake word: social greeting or phone talk
        self.opening: str | None = None
        self.in_conversation = threading.Event()

    def serve_remote(self) -> None:
        """127.0.0.1:8003 — someone holds the PWA's talk button: their phone audio replaces
        the robot's mic for the rest of that conversation (it ends as usual when quiet)."""
        srv = socket.create_server(("127.0.0.1", REMOTE_PORT))
        while True:
            conn, _ = srv.accept()
            with conn:
                log.info("phone talk: start")
                self.mic.set_remote(True)
                if not self.in_conversation.is_set():
                    self.opening = None
                    self.greet.set()
                while chunk := conn.recv(4096):
                    self.mic.inject(chunk)
                # Released: a moment of silence so the AI's VAD knows the turn is over.
                self.mic.inject(bytes(int(END_OF_TURN_SILENCE_S * 16000) * 2))
                log.info("phone talk: released")

    def serve_local(self) -> None:
        """127.0.0.1:8001 — POST /greet: start a conversation without the wake word."""
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != "/greet":
                    return self.send_error(404)
                service.opening = GREETING.format(me=introduce(service.s.owner_name))
                service.greet.set()
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                pass

        ThreadingHTTPServer(("127.0.0.1", GREET_PORT), Handler).serve_forever()

    async def conversation(self, opening: str | None = None) -> None:
        s = self.s
        # Muted through the beep (so it is not sent as speech); reopened right after.
        asyncio.get_running_loop().call_later(self.player.remaining_s(), self.mic.mute, False)
        state = await asyncio.to_thread(self.backend.state)
        memory = await asyncio.to_thread(self.backend.recall)
        prompt = instructions(state, wake_phrase(s.wake_model), memory, s.owner_name)
        talk = LiveConversation(self.ai, prompt,
                                self.mic, self.player, self.vad, self.backend, s.idle_s,
                                camera.capture_jpeg, opening=opening, full_photo=camera.full_photo)
        try:
            await talk.run()
        except Exception as exc:  # noqa: BLE001 - network/API: say so, back to the wake word
            log.warning("conversation failed: %r", exc)
            # An empty AI balance looks like any other API error: say which one it is.
            self.player.write(self.no_credit if self.ai.out_of_credit(exc) else self.no_network)
            await asyncio.sleep(self.player.remaining_s())

    def run(self) -> None:
        self.mic.start()
        threading.Thread(target=self.backend.heartbeat, name="heartbeat", daemon=True).start()
        threading.Thread(target=self.serve_local, name="greet", daemon=True).start()
        threading.Thread(target=self.serve_remote, name="phone-talk", daemon=True).start()
        self.backend.report("idle")
        log.info("voice ready (wake word %r, %s model %s)", self.s.wake_model, self.s.ai_provider, self.ai.model)
        while True:
            woke = self.wake.wait(self.greet)
            opening = None if woke is not None else self.opening
            self.in_conversation.set()
            # The beep plays while the session connects; speech captured from
            # now on waits in the mic queue, so the first words are not lost.
            self.mic.mute(True)
            self.player.write(sfx.chirps(3) if opening else self.beep)
            self.backend.report("listening")
            try:
                asyncio.run(self.conversation(opening))
            finally:
                self.in_conversation.clear()
                self.mic.set_remote(False)
                self.greet.clear()  # a greeting asked for mid-conversation is stale now
                self.backend.report("idle")


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    s = VoiceSettings.from_env()
    if not s.ai_api_key:
        log.warning("AI_API_KEY is empty: conversations will fail (see pi/.env.example)")
    VoiceService(s).run()


if __name__ == "__main__":
    main()
