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
from pathlib import Path

from .audio import MIC_RATE

log = logging.getLogger("robert.voice.live")

UNMUTE_TAIL_S = 0.15
FOLLOW_WAIT_STEPS, FOLLOW_WAIT_STEP_S = 12, 0.3   # acquisition gives up after 3 s
WATCH_S = 0.2
DIRECTIONS = {"adelante": "forward", "atras": "backward", "izquierda": "left", "derecha": "right"}

SYSTEM_PROMPT = """Eres Robert, un pequeño robot rover de cuatro ruedas hecho en casa por Fran.
Tienes un LiDAR que ve 360° a tu alrededor, una cámara delante, micrófonos y altavoces.
Hablas español de España, con gracia y cercanía, como un compañero curioso.
Contesta breve: una o dos frases, salvo que te pidan más.

Herramientas:
- parar: en cuanto te pidan parar o quedarte quieto, o ante cualquier duda de seguridad.
- mover: avanza, retrocede o gira un instante (un segundo como mucho). Si la respuesta
  dice que no se ha podido, explícalo.
- mirar: hace una foto con tu cámara delantera y te la envía. Úsala siempre que te
  pregunten qué ves, quién está, qué es algo o cómo es lo que tienes delante. Describe
  solo lo que aparece en la foto; si no se ve bien, dilo.
- estado_robot: distancias del LiDAR alrededor y si te mueves. No inventes distancias.
- seguirme: empieza a seguir a la persona que tienes delante (a 1,2 m). Úsala cuando te
  pidan que les sigas o les acompañes. Si responde que no ve a nadie, pide que se ponga
  delante de ti, a menos de 3 m. Para parar: dejar_de_seguir (o parar).
- despedirse: cuando la persona se despida o diga que ya está.
Explorar no se activa por voz: di que se hace desde la app.
Te despiertan diciendo "{wake}"; es la señal de activación, no tu nombre. Si solo oyes
eso, no saludes ni te presentes: espera a la petición o di como mucho "¿Sí?"."""


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


def wake_phrase(model: str) -> str:
    """'hey_jarvis' or '/path/robert.onnx' -> 'Hey Jarvis' / 'Robert'."""
    name = Path(model).stem.split("_v0")[0]
    return name.replace("_", " ").title()


def session_config(voice: str, silence_ms: int, state: dict | None, wake: str):
    from google.genai import types

    def fn(name, description, parameters=None):
        return types.FunctionDeclaration(name=name, description=description, parameters=parameters)

    tools = [types.Tool(function_declarations=[
        fn("parar", "Para el robot inmediatamente."),
        fn("mover", "Mueve el robot un instante (un segundo como máximo).",
           {"type": "object", "required": ["direccion"],
            "properties": {"direccion": {"type": "string", "enum": list(DIRECTIONS)}}}),
        fn("mirar", "Hace una foto con la cámara delantera y la envía para que la veas."),
        fn("estado_robot", "Devuelve lo que el robot sabe ahora: espacio libre alrededor y si se mueve."),
        fn("seguirme", "Empieza a seguir a la persona que está delante del robot."),
        fn("dejar_de_seguir", "Deja de seguir a la persona y se para."),
        fn("despedirse", "Termina la conversación cuando la persona se despide."),
    ])]
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=SYSTEM_PROMPT.format(wake=wake) + "\n\n" + describe_state(state),
        tools=tools,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                silence_duration_ms=silence_ms, prefix_padding_ms=100)),
    )


class LiveConversation:
    """One conversation, from just after the wake word until idle or goodbye."""

    def __init__(self, client, model: str, config, mic, player, vad, backend, idle_s: float,
                 camera: Callable[[], bytes | None] = lambda: None,
                 clock: Callable[[], float] = time.monotonic):
        self._client = client
        self._model = model
        self._config = config
        self._mic = mic
        self._player = player
        self._vad = vad
        self._backend = backend
        self._idle_s = idle_s
        self._camera = camera
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

    async def _start_follow(self) -> dict:
        """Starts follow-me and waits (briefly) to know whether a person was found."""
        result = await asyncio.to_thread(self._backend.command, "follow")
        if not result.get("ok"):
            return result
        for _ in range(FOLLOW_WAIT_STEPS):
            await asyncio.sleep(FOLLOW_WAIT_STEP_S)
            state = await asyncio.to_thread(self._backend.state) or {}
            follow = state.get("nav", {}).get("follow", {})
            if follow.get("state") == "following":
                return {"ok": True, "estado": f"te sigo; estás a {follow.get('distance_m')} m"}
            if follow.get("state") in ("stopped", "failed", "idle"):
                return {"ok": False, "reason": "no veo a nadie delante, a menos de 3 m"}
        return {"ok": False, "reason": "no veo a nadie delante, a menos de 3 m"}

    async def _tools(self, calls) -> None:
        from google.genai import types

        responses = []
        for call in calls:
            result = await self._tool(call.name, dict(call.args or {}))
            parts = None
            if isinstance(result, bytes):  # a photo: hand it back with the call it answers
                parts = [types.FunctionResponsePart(inline_data=types.FunctionResponseBlob(
                    mime_type="image/jpeg", data=result))]
                result = {"resultado": "Esta es la foto que acaba de hacer tu cámara delantera."}
            log.info("tool %s(%s) -> %s%s", call.name, dict(call.args or {}), result,
                     " + photo" if parts else "")
            responses.append(types.FunctionResponse(id=call.id, name=call.name, response=result,
                                                    parts=parts))
        await self._session.send_tool_response(function_responses=responses)

    async def _tool(self, name: str, args: dict) -> dict | bytes:
        if name == "parar":
            return await asyncio.to_thread(self._backend.command, "stop")
        if name == "mover":
            action = DIRECTIONS.get(args.get("direccion"))
            if action is None:
                return {"ok": False, "reason": "dirección desconocida"}
            return await asyncio.to_thread(self._backend.command, action)
        if name == "seguirme":
            return await self._start_follow()
        if name == "dejar_de_seguir":
            return await asyncio.to_thread(self._backend.command, "follow_off")
        if name == "mirar":
            jpeg = await asyncio.to_thread(self._camera)
            return jpeg if jpeg is not None else {"ok": False, "reason": "la cámara no responde"}
        if name == "estado_robot":
            state = await asyncio.to_thread(self._backend.state)
            return {"estado": describe_state(state)}
        if name == "despedirse":
            self._goodbye = True
            return {"ok": True}
        return {"ok": False, "reason": "herramienta desconocida"}
