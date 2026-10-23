"""Robert voice service: wake word -> Gemini Live conversation, as its own process.

    python -m robert.voice.service        (from pi/, reads pi/.env)

Runs apart from the backend so audio and network latency never touch motor
control. It only talks to the backend over localhost: it reports what it
heard/said, and forwards spoken commands that the backend bounds and guards.
"""

import asyncio
import logging
import threading

from . import assets
from .audio import Mic, StreamPlayer
from .backend import Backend
from .config import VoiceSettings
from .listener import Vad, WakeWord
from .live import LiveConversation, session_config, wake_phrase

log = logging.getLogger("robert.voice")


class VoiceService:
    def __init__(self, s: VoiceSettings):
        from google import genai

        self.s = s
        self.client = genai.Client(api_key=s.gemini_api_key)
        self.mic = Mic(s.alsa_device)
        self.player = StreamPlayer(s.alsa_device)
        self.vad = Vad()
        self.wake = WakeWord(self.mic, s.wake_model, s.wake_threshold)
        self.backend = Backend(s.backend_url)
        self.beep = assets.beep()
        self.no_network = assets.phrase("no_network", s.assets_dir, s.piper_voice)

    async def conversation(self) -> None:
        s = self.s
        # Muted through the beep (so it is not sent as speech); reopened right after.
        asyncio.get_running_loop().call_later(self.player.remaining_s(), self.mic.mute, False)
        state = await asyncio.to_thread(self.backend.state)
        talk = LiveConversation(self.client, s.live_model, session_config(s.voice, s.silence_ms, state, wake_phrase(s.wake_model)),
                                self.mic, self.player, self.vad, self.backend, s.idle_s)
        try:
            await talk.run()
        except Exception as exc:  # noqa: BLE001 - network/API: say so, back to the wake word
            log.warning("conversation failed: %r", exc)
            self.player.write(self.no_network)
            await asyncio.sleep(self.player.remaining_s())

    def run(self) -> None:
        self.mic.start()
        threading.Thread(target=self.backend.heartbeat, name="heartbeat", daemon=True).start()
        self.backend.report("idle")
        log.info("voice ready (wake word %r, model %s)", self.s.wake_model, self.s.live_model)
        while True:
            self.wake.wait()
            # The beep plays while the session connects; speech captured from
            # now on waits in the mic queue, so the first words are not lost.
            self.mic.mute(True)
            self.player.write(self.beep)
            self.backend.report("listening")
            try:
                asyncio.run(self.conversation())
            finally:
                self.backend.report("idle")


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    s = VoiceSettings.from_env()
    if not s.gemini_api_key:
        log.warning("GEMINI_API_KEY is empty: conversations will fail")
    VoiceService(s).run()


if __name__ == "__main__":
    main()
