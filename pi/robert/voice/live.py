"""A spoken conversation with Gemini Live: audio in, audio out, streaming.

After the wake word, one WebSocket session carries the microphone live to
Gemini; its server-side VAD ends each turn, and the reply audio is played
chunk by chunk as it arrives. Robert's actions are function calls that go to
the backend, which bounds and guards them; voice can never start autonomy.

Half-duplex: the ReSpeaker has no echo cancellation, so the mic is muted
while Robert speaks (otherwise he would interrupt himself).
"""

import asyncio
import logging
import time
from collections.abc import Callable

from .audio import MIC_RATE

log = logging.getLogger("robert.voice.live")

UNMUTE_TAIL_S = 0.15
WATCH_S = 0.2
DIRECTIONS = {"adelante": "forward", "atras": "backward", "izquierda": "left", "derecha": "right"}

SYSTEM_PROMPT = """Eres Robert, un pequeño robot rover de cuatro ruedas hecho en casa por Fran.
Tienes un LiDAR que ve 360° a tu alrededor, una cámara, micrófonos y altavoces.
Hablas español de España, con gracia y cercanía, como un compañero curioso.
Contesta breve: una o dos frases, salvo que te pidan más.

Herramientas:
- parar: en cuanto te pidan parar o quedarte quieto, o ante cualquier duda de seguridad.
- mover: avanza, retrocede o gira un instante (un segundo como mucho). Si la respuesta
  dice que no se ha podido, explícalo.
- estado_robot: úsala si te preguntan qué ves, qué tienes cerca o cómo estás. No inventes
  distancias ni datos que no te dé.
- despedirse: cuando la persona se despida o diga que ya está.
Explorar o seguir a alguien no se activa por voz: di que se hace desde la app."""


def describe_state(state: dict | None) -> str:
    """Plain facts for the model. Distances come from the guard's clearances,
    which exclude returns from the robot's own body (the raw LiDAR does not)."""
    if not state:
        return "Estado del robot: desconocido."
    facts = ["motores conectados" if state.get("esp32", {}).get("connected") else "motores desconectados"]
    clear = state.get("nav", {}).get("clearance") or {}
    for side, label in (("front", "delante"), ("back", "detrás"), ("left", "a la izquierda"),
                        ("right", "a la derecha")):
        if clear.get(side) is not None:
            facts.append(f"espacio libre {label}: {clear[side]:.1f} m")
    if not clear:
        facts.append("sin datos del LiDAR")
    motion = state.get("motion", {})
    facts.append("parado" if motion.get("mode") == "idle" else f"moviéndose ({motion.get('mode')})")
    return "Estado del robot: " + ", ".join(facts) + "."


def session_config(voice: str, silence_ms: int, state: dict | None):
    from google.genai import types

    def fn(name, description, parameters=None):
        return types.FunctionDeclaration(name=name, description=description, parameters=parameters)

    tools = [types.Tool(function_declarations=[
        fn("parar", "Para el robot inmediatamente."),
        fn("mover", "Mueve el robot un instante (un segundo como máximo).",
           {"type": "object", "required": ["direccion"],
            "properties": {"direccion": {"type": "string", "enum": list(DIRECTIONS)}}}),
        fn("estado_robot", "Devuelve lo que el robot sabe ahora: espacio libre alrededor y si se mueve."),
        fn("despedirse", "Termina la conversación cuando la persona se despide."),
    ])]
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=SYSTEM_PROMPT + "\n\n" + describe_state(state),
        tools=tools,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                silence_duration_ms=silence_ms, prefix_padding_ms=100)),
    )


class LiveConversation:
    """One conversation, from just after the wake word until idle or goodbye."""

    def __init__(self, client, model: str, config, mic, player, vad, backend, idle_s: float,
                 clock: Callable[[], float] = time.monotonic):
        self._client = client
        self._model = model
        self._config = config
        self._mic = mic
        self._player = player
        self._vad = vad
        self._backend = backend
        self._idle_s = idle_s
        self._clock = clock
        self._session = None
        self._last_voice = self._last_sound = clock()
        self._speaking = False          # a reply is arriving or playing
        self._goodbye = False
        self._heard = self._said = ""
        self._first_audio = True
        self._unmute: asyncio.Task | None = None

    async def run(self) -> None:
        async with self._client.aio.live.connect(model=self._model, config=self._config) as session:
            self._session = session
            tasks = [asyncio.create_task(self._send_mic()), asyncio.create_task(self._receive()),
                     asyncio.create_task(self._watch())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in done:  # a crashed task (dropped socket, bad message) ends the talk loudly
                    if not t.cancelled() and t.exception():
                        raise t.exception()
            finally:
                for t in tasks + ([self._unmute] if self._unmute else []):
                    t.cancel()
                self._mic.mute(False)

    async def _send_mic(self) -> None:
        from google.genai import types

        mime = f"audio/pcm;rate={MIC_RATE}"
        while True:
            frame = await asyncio.to_thread(self._mic.read, WATCH_S)
            if frame is None:  # muted or no audio yet
                continue
            if self._vad.is_speech(frame):
                self._last_voice = self._clock()
            await self._session.send_realtime_input(audio=types.Blob(data=frame.tobytes(), mime_type=mime))

    async def _watch(self) -> None:
        """Ends the conversation after a goodbye or `idle_s` without anyone talking."""
        while True:
            await asyncio.sleep(WATCH_S)
            if self._speaking or self._player.busy:
                continue
            quiet = self._clock() - max(self._last_voice, self._last_sound)
            if self._goodbye or quiet > self._idle_s:
                log.info("conversation over (%s)", "goodbye" if self._goodbye else "idle")
                return

    async def _receive(self) -> None:
        while True:  # receive() yields one model turn, then returns
            async for msg in self._session.receive():
                if msg.go_away:
                    log.info("server asked to close the session")
                    return
                if msg.tool_call:
                    await self._tools(msg.tool_call.function_calls)
                if msg.server_content:
                    self._content(msg.server_content)

    def _content(self, sc) -> None:
        if sc.input_transcription and sc.input_transcription.text:
            self._heard += sc.input_transcription.text
        if sc.output_transcription and sc.output_transcription.text:
            self._said += sc.output_transcription.text
        if sc.model_turn:
            for part in sc.model_turn.parts or []:
                if part.inline_data and part.inline_data.data:
                    self._audio(part.inline_data.data)
        if sc.turn_complete:
            self._turn_done()

    def _audio(self, pcm: bytes) -> None:
        if self._first_audio:
            self._first_audio = False
            self._mic.mute(True)
            log.info("reply %.2f s after speech; heard %r", self._clock() - self._last_voice,
                     self._heard.strip())
            self._backend.report("speaking", self._heard.strip() or None)
        self._speaking = True
        if self._unmute:
            self._unmute.cancel()
            self._unmute = None
        self._player.write(pcm)

    def _turn_done(self) -> None:
        heard, said = self._heard.strip(), self._said.strip()
        if said:
            log.info("said %r", said)
        self._backend.report("listening", heard or None, said or None)
        self._heard = self._said = ""
        self._first_audio = True
        self._unmute = asyncio.create_task(self._reopen_mic())

    async def _reopen_mic(self) -> None:
        await asyncio.sleep(self._player.remaining_s() + UNMUTE_TAIL_S)
        self._speaking = False
        self._last_sound = self._clock()
        self._mic.mute(False)

    async def _tools(self, calls) -> None:
        from google.genai import types

        responses = []
        for call in calls:
            result = await self._tool(call.name, dict(call.args or {}))
            log.info("tool %s(%s) -> %s", call.name, dict(call.args or {}), result)
            responses.append(types.FunctionResponse(id=call.id, name=call.name, response=result))
        await self._session.send_tool_response(function_responses=responses)

    async def _tool(self, name: str, args: dict) -> dict:
        if name == "parar":
            return await asyncio.to_thread(self._backend.command, "stop")
        if name == "mover":
            action = DIRECTIONS.get(args.get("direccion"))
            if action is None:
                return {"ok": False, "reason": "dirección desconocida"}
            return await asyncio.to_thread(self._backend.command, action)
        if name == "estado_robot":
            state = await asyncio.to_thread(self._backend.state)
            return {"estado": describe_state(state)}
        if name == "despedirse":
            self._goodbye = True
            return {"ok": True}
        return {"ok": False, "reason": "herramienta desconocida"}
