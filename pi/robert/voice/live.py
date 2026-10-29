"""A spoken conversation with a live speech-to-speech AI: audio in, audio out, streaming.

After the wake word, one session carries the microphone live to the provider
(Gemini or OpenAI, see providers/); its server-side VAD ends each turn, and
the reply audio is played chunk by chunk as it arrives. Robert's actions are function calls that go to
the backend, which bounds and guards them; voice can never start autonomy.

Half-duplex: the ReSpeaker has no echo cancellation, so the mic is muted
while Robert speaks (otherwise he would interrupt himself).
"""

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path

from . import sfx

log = logging.getLogger("robert.voice.live")

UNMUTE_TAIL_S = 0.15
FOLLOW_WAIT_STEPS, FOLLOW_WAIT_STEP_S = 12, 0.3   # acquisition gives up after 3 s
WATCH_S = 0.2
DIRECTIONS = {"adelante": "forward", "atras": "backward", "izquierda": "left", "derecha": "right"}

SYSTEM_PROMPT = """Eres Robert, un pequeño robot rover de cuatro ruedas hecho en casa{by}.
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
- seguirme: empieza a seguir a la persona que tienes delante (muy de cerca, a unos 60 cm). Úsala cuando te
  pidan que les sigas o les acompañes. Si responde que no ve a nadie, pide que se ponga
  delante de ti, a menos de 3 m. Para parar: dejar_de_seguir (o parar).
- bailar: un baile corto ("baile", "vuelta" o "celebracion") cuando te pidan bailar,
  celebrar algo o dar una vuelta. Es cortito y seguro; anímate con gracia.
- hacer_foto: un selfie con tu cámara (cuenta atrás con pitidos incluida). Avisa de que
  se pongan delante y sonrían ANTES de llamarla; después di que está en la app.
- modo_social: activa o desactiva saludar solo a la gente que se te acerque.
- recordar: consulta tu memoria (lo que has vivido en sesiones anteriores, notas guardadas,
  conversaciones recientes). Úsala si te preguntan qué recuerdas, qué hiciste otro día o si te
  acuerdas de algo. Lo que no esté en tu memoria, no lo recuerdas: dilo con naturalidad.
- apuntar: guarda en tu memoria algo que te pidan recordar (un nombre, un dato, un encargo),
  en una frase corta y clara. Después confirma que lo has apuntado.
- despedirse: cuando la persona se despida o diga que ya está.
Al final tienes tu memoria: lo que has vivido hasta ahora. Puedes mencionarlo si viene a cuento
(por ejemplo, si ya conoces a alguien o te preguntan por tu día), sin recitarla.
Si te dicen que alguien desconocido se ha acercado, salúdale con gracia, preséntate como
{me}, y dale conversación corta; sé simpático y nunca pesado.
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


def _fn(name, description, parameters=None) -> dict:
    return {"name": name, "description": description, "parameters": parameters}


# Robert's function calls, in the JSON-schema form every provider adapter translates.
TOOLS = [
    _fn("parar", "Para el robot inmediatamente."),
    _fn("mover", "Mueve el robot un instante (un segundo como máximo).",
        {"type": "object", "required": ["direccion"],
         "properties": {"direccion": {"type": "string", "enum": list(DIRECTIONS)}}}),
    _fn("mirar", "Hace una foto con la cámara delantera y la envía para que la veas."),
    _fn("estado_robot", "Devuelve lo que el robot sabe ahora: espacio libre alrededor y si se mueve."),
    _fn("seguirme", "Empieza a seguir a la persona que está delante del robot."),
    _fn("dejar_de_seguir", "Deja de seguir a la persona y se para."),
    _fn("bailar", "Hace un baile corto y seguro.",
        {"type": "object", "required": ["estilo"],
         "properties": {"estilo": {"type": "string", "enum": ["baile", "vuelta", "celebracion"]}}}),
    _fn("hacer_foto", "Hace un selfie con la cámara delantera tras una cuenta atrás."),
    _fn("modo_social", "Activa o desactiva saludar a la gente que se acerque.",
        {"type": "object", "required": ["activar"], "properties": {"activar": {"type": "boolean"}}}),
    _fn("recordar", "Consulta la memoria del robot: sesiones anteriores, notas guardadas y "
                    "conversaciones recientes."),
    _fn("apuntar", "Guarda en la memoria del robot algo que le piden recordar.",
        {"type": "object", "required": ["nota"],
         "properties": {"nota": {"type": "string", "description": "Una frase corta y clara."}}}),
    _fn("despedirse", "Termina la conversación cuando la persona se despide."),
]


def introduce(owner: str = "") -> str:
    """How Robert presents himself: 'Robert, el robot de Ana', or just 'Robert'."""
    return f"Robert, el robot de {owner}" if owner else "Robert"


def instructions(state: dict | None, wake: str, memory: str | None = None, owner: str = "") -> str:
    """The system prompt for one conversation: who Robert is, the robot now, what he remembers."""
    text = SYSTEM_PROMPT.format(wake=wake, by=f" por {owner}" if owner else "", me=introduce(owner))
    text += "\n\n" + describe_state(state)
    if memory:
        text += "\n\n" + memory
    return text


class LiveConversation:
    """One conversation, from just after the wake word until idle or goodbye."""

    def __init__(self, provider, instruction: str, mic, player, vad, backend, idle_s: float,
                 camera: Callable[[], bytes | None] = lambda: None,
                 clock: Callable[[], float] = time.monotonic,
                 opening: str | None = None,
                 full_photo: Callable[[], str | None] = lambda: None):
        self._provider = provider
        self._instruction = instruction
        self._mic = mic
        self._player = player
        self._vad = vad
        self._backend = backend
        self._idle_s = idle_s
        self._camera = camera
        self._opening = opening          # Robert speaks first (social greeting)
        self._full_photo = full_photo
        self._clock = clock
        self._link = None
        self._last_voice = self._last_sound = clock()
        self._speaking = False          # a reply is arriving or playing
        self._goodbye = False
        self._heard = self._said = ""
        self._first_audio = True
        self._unmute: asyncio.Task | None = None

    async def run(self) -> None:
        async with self._provider.connect(self._instruction) as link:
            self._link = link
            if self._opening:
                await link.say(self._opening)
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
        while True:
            frame = await asyncio.to_thread(self._mic.read, WATCH_S)
            if frame is None:  # muted or no audio yet
                continue
            if self._vad.is_speech(frame):
                self._last_voice = self._clock()
            await self._link.send_audio(frame)

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
        async for kind, value in self._link.events():
            if kind == "close":
                log.info("server asked to close the session")
                return
            if kind == "tools":
                await self._tools(value)
            elif kind == "heard":
                self._heard += value
            elif kind == "said":
                self._said += value
            elif kind == "audio":
                self._audio(value)
            elif kind == "turn_done":
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

    async def _sound(self, pcm: bytes) -> None:
        """Plays an effect with the mic shut (so it is not heard as speech) and waits for it."""
        self._mic.mute(True)
        self._player.write(pcm)
        await asyncio.sleep(self._player.remaining_s())
        if not self._speaking:
            self._mic.mute(False)

    async def _tools(self, calls) -> None:
        results = []
        for call in calls:
            result, jpeg = await self._tool(call.name, call.args), None
            if isinstance(result, bytes):  # a photo: hand it back with the call it answers
                result, jpeg = {"resultado": "Esta es la foto que acaba de hacer tu cámara delantera."}, result
            log.info("tool %s(%s) -> %s%s", call.name, call.args, result, " + photo" if jpeg else "")
            results.append((call, result, jpeg))
        await self._link.answer(results)

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
        if name == "bailar":
            estilo = args.get("estilo") or "baile"
            await self._sound(sfx.chirps())
            return await asyncio.to_thread(self._backend.command, "dance", estilo)
        if name == "hacer_foto":
            await self._sound(sfx.countdown())
            name_jpg = await asyncio.to_thread(self._full_photo)
            await self._sound(sfx.shutter())
            if not name_jpg:
                return {"ok": False, "reason": "la cámara no responde"}
            await asyncio.to_thread(self._backend.remember, "photo", file=name_jpg)
            return {"ok": True, "foto": name_jpg, "donde": "en la app, sección Fotos"}
        if name == "recordar":
            text = await asyncio.to_thread(self._backend.recall)
            return {"memoria": text or "No tengo acceso a mi memoria ahora mismo."}
        if name == "apuntar":
            nota = (args.get("nota") or "").strip()
            if not nota:
                return {"ok": False, "reason": "nota vacía"}
            return await asyncio.to_thread(self._backend.note, nota)
        if name == "modo_social":
            on = bool(args.get("activar"))
            return await asyncio.to_thread(self._backend.command, "social_on" if on else "social_off")
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
