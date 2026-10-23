"""Gemini hears the recorded audio directly and answers as Robert.

One call does both understanding and replying, with structured JSON output:
{"heard": what the person said, "reply": what Robert says, "command": ...}.
`command` comes from a closed menu; anything else becomes "none", and the
backend still decides whether (and how far) the robot actually moves.
"""

import json
import logging
from collections import deque
from dataclasses import dataclass

log = logging.getLogger("robert.voice.brain")

COMMANDS = ("none", "stop", "forward", "backward", "left", "right")
HISTORY_TURNS = 6

SYSTEM_PROMPT = """Eres Robert, un pequeño robot rover de cuatro ruedas hecho en casa por Fran.
Tienes un LiDAR que ve 360° a tu alrededor, una cámara, micrófonos y altavoces.
Hablas español de España, con gracia y cercanía, como un compañero curioso.

Responde SIEMPRE en una o dos frases cortas: lo que digas se convierte en voz.
Nada de emojis, listas, markdown ni símbolos raros.

Rellena "heard" con lo que ha dicho la persona, palabra por palabra. Si el audio no
contiene una frase dirigida a ti (ruido, silencio, gente hablando de fondo), deja
"heard" y "reply" vacíos.

"command" mueve el robot un instante (como mucho un segundo):
- "stop": te piden que pares o te quedes quieto. Úsalo ante cualquier duda de seguridad.
- "forward" / "backward": avanzar o retroceder un poco.
- "left" / "right": girar un poco hacia ese lado.
- "none": en cualquier otro caso. Explorar o seguir a alguien NO lo haces por voz:
  di que se activa desde la app.
El estado del robot que te paso es real: úsalo si te preguntan qué ves o cómo estás,
sin inventar datos que no estén ahí."""

SCHEMA = {
    "type": "object",
    "properties": {
        "heard": {"type": "string"},
        "reply": {"type": "string"},
        "command": {"type": "string", "enum": list(COMMANDS)},
    },
    "required": ["heard", "reply", "command"],
}


@dataclass(frozen=True)
class Turn:
    heard: str
    reply: str
    command: str


def parse_turn(text: str) -> Turn:
    """Validates the model output; anything malformed becomes an empty turn."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        log.warning("model returned non-JSON: %.80r", text)
        return Turn("", "", "none")
    if not isinstance(data, dict):
        return Turn("", "", "none")
    command = data.get("command")
    return Turn(
        heard=str(data.get("heard") or "").strip(),
        reply=str(data.get("reply") or "").strip(),
        command=command if command in COMMANDS else "none",
    )


def describe_state(state: dict | None) -> str:
    """A few plain facts about the robot for the prompt."""
    if not state:
        return "Estado del robot: desconocido."
    facts = []
    esp = state.get("esp32", {})
    facts.append("motores conectados" if esp.get("connected") else "motores desconectados")
    lidar = state.get("lidar", {})
    if lidar.get("connected") and lidar.get("nearest_mm"):
        facts.append(f"objeto más cercano a {lidar['nearest_mm'] / 1000:.1f} m")
    clear = state.get("nav", {}).get("clearance") or {}
    for side, label in (("front", "delante"), ("back", "detrás"), ("left", "izquierda"), ("right", "derecha")):
        if clear.get(side) is not None:
            facts.append(f"libre {label}: {clear[side]:.1f} m")
    motion = state.get("motion", {})
    facts.append("parado" if motion.get("mode") == "idle" else f"moviéndose ({motion.get('mode')})")
    return "Estado del robot: " + ", ".join(facts) + "."


class Brain:
    def __init__(self, client, model: str):
        self._client = client
        self._model = model
        self._history: deque[tuple[str, str]] = deque(maxlen=HISTORY_TURNS)

    def understand(self, wav: bytes, robot_state: dict | None) -> Turn:
        from google.genai import types

        contents = []
        for heard, reply in self._history:
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=heard)]))
            contents.append(types.Content(role="model", parts=[types.Part.from_text(text=reply)]))
        contents.append(types.Content(role="user", parts=[
            types.Part.from_text(text=describe_state(robot_state)),
            types.Part.from_bytes(data=wav, mime_type="audio/wav"),
        ]))
        response = self._client.models.generate_content(
            model=self._model, contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=SCHEMA,
                temperature=0.8,
            ))
        turn = parse_turn(response.text)
        if turn.heard and turn.reply:
            self._history.append((turn.heard, turn.reply))
        return turn
