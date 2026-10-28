# La memoria de Robert

Robert apunta lo que vive en cada sesión y se lleva un resumen de una sesión a la siguiente.
Es deliberadamente simple: dos ficheros JSON, sin base de datos ni modelo que resuma nada.
El código vive en `pi/robert/memory.py`; la voz lo usa desde `pi/robert/voice/live.py`.

```
~/robert-memory/                 (MEMORY_DIR)
├── memory.json                  lo que perdura: totales, últimas sesiones, vivencias, notas, charlas
└── sessions/
    ├── 20261025-103012.jsonl    diario de una sesión (un arranque del backend): un evento por línea
    └── ...                      se conservan las 30 últimas
```

## Qué recuerda

Una **sesión** es un arranque del backend (`robert.service`). Durante la sesión el backend anota
eventos en el diario de dos maneras:

- **Lo que le cuentan.** El servicio de voz informa de cada turno de conversación (`talk`: lo que oyó y
  lo que contestó), de cada orden hablada que ejecuta (`command`), de las fotos (`photo`) y de las notas
  que alguien le pide guardar (`note`). El modo social anota a quién saluda (`greeting`).
- **Lo que observa.** Cada vuelta del bucle de navegación (10 Hz) `Memory.observe()` compara el
  `RobotState` con la vuelta anterior y apunta solo los cambios que importan: `esp32_connected` /
  `esp32_lost`, `failsafe` (parada de seguridad del ESP32, con la distancia del ultrasonidos),
  `conversation` / `conversation_end` (con su duración), `follow` / `follow_end` (con el motivo:
  `follow_lost`, `follow_too_far`…), `explore`, `stuck` y `dance`.

Además hay **vivencias** (`story`): recuerdos contados en primera persona, como el día que salió a la
calle. Las primeras vienen en `pi/memory_seed.json` (en el repo) y se añaden a `memory.json` una sola
vez, en el primer arranque que no las tenga. Se pueden añadir más por HTTP (abajo).

Cada evento es una línea JSON con `t` (hora local ISO), `kind` y sus datos:

```json
{"t": "2026-10-25T12:03:11+01:00", "kind": "talk", "heard": "¿qué ves?", "reply": "Veo una mesa."}
{"t": "2026-10-25T12:05:40+01:00", "kind": "follow_end", "reason": "follow_lost"}
{"t": "2026-10-25T12:09:02+01:00", "kind": "failsafe", "failsafe": "ultrasonic", "distance_cm": 12.0}
```

`memory.json` se reescribe (de forma atómica) con cada evento:

```json
{
  "schema": 1,
  "first_boot": "2026-10-22T18:40:03+02:00",
  "boots": 12,
  "totals": {"conversation": 40, "talk": 212, "photo": 9, "dance": 4, "follow": 7, "greeting": 5},
  "sessions": [{"id": "20261025-103012", "started": "…", "ended": "…", "duration_s": 2460, "counts": {"photo": 2}}],
  "stories": [{"t": "2026-10-25", "text": "Mi primera salida a la calle, con Fran…"}],
  "notes": [{"t": "…", "text": "Marta es la vecina del segundo"}],
  "recent_talks": [{"t": "…", "heard": "…", "reply": "…"}],
  "recent_events": [ …los 50 últimos eventos que no son rutina… ]
}
```

Límites (en `memory.py`): 30 sesiones, 100 vivencias, 100 notas, 30 turnos de charla, 50 eventos
recientes; textos recortados a 300 caracteres.

## Cómo la usa la voz

Al abrir cada conversación, el servicio de voz pide `GET /api/memory/recall` y pega el texto al final
del prompt de Gemini. Son unas pocas líneas en español, acotadas (últimas 10 vivencias, 10 notas y 5
charlas), por ejemplo:

```
Tu memoria: te han encendido 12 veces desde el 2026-10-22.
En total: 40 conversaciones, 9 fotos, 4 bailes, 7 veces siguiendo a alguien, 5 saludos a desconocidos.
Última sesión: 2026-10-27 18:02, duró 41 min; 3 conversaciones, 2 fotos.
Esta sesión: llevas 12 min encendido; 1 conversación.
Lo que has vivido (tus recuerdos, contados por ti):
- (2026-10-25) Mi primera salida a la calle, con Fran. No las tenía todas conmigo, pero salí…
Cosas que te han pedido recordar:
- (2026-10-25 12:40) Marta es la vecina del segundo
Últimas conversaciones:
- (2026-10-27 18:10) te dijeron «¿qué ves?» y contestaste «Veo una mesa y una silla.».
```

Dos herramientas nuevas para el modelo:

- `recordar`: devuelve ese mismo texto cuando le preguntan qué recuerda o qué hizo otro día.
- `apuntar(nota)`: guarda una nota («acuérdate de que me llamo Marta»); Robert confirma que lo ha apuntado.

Lo que no esté en el texto, Robert no lo recuerda, y el prompt le pide que lo diga con naturalidad.

## API

| Método | Ruta | Quién | Qué |
|---|---|---|---|
| GET | `/api/memory` | cualquiera en la LAN | `memory.json` |
| GET | `/api/memory/session` | cualquiera | el diario de la sesión actual |
| GET | `/api/memory/recall` | cualquiera | el texto que va al prompt |
| POST | `/api/memory/note` `{"text"}` | solo localhost | guarda una nota |
| POST | `/api/memory/story` `{"text", "when"?}` | solo localhost | guarda una vivencia (`when`: `"2026-10-25"` si no es de hoy) |
| POST | `/api/memory/event` `{"kind", "data"}` | solo localhost | evento genérico (la voz lo usa para las fotos) |

Las escrituras son solo desde la Pi, como el resto de rutas de la voz. Para añadir una vivencia, desde
una sesión SSH en la Pi:

```sh
curl -s -XPOST localhost:8000/api/memory/story -H 'Content-Type: application/json' \
  -d '{"text": "Hoy he bailado en el salón para los vecinos.", "when": "2026-10-29"}'
```

## Dónde vive y cómo no perderla

`MEMORY_DIR` (por defecto `~/robert-memory`, en `pi/.env`). Con la SD protegida
(`setup_pi.sh protect`, overlay de solo lectura) todo lo que se escribe en la Pi vive en RAM y **se
pierde al apagar**, igual que las fotos. Para conservarla:

```sh
pi/scripts/pull_memory.sh        # Pi -> Mac, a pi/memory/ (fuera de git), antes de apagar
pi/scripts/pull_memory.sh push   # Mac -> Pi, tras reinstalar o al volver a protegerla
```

Si la carpeta no se puede escribir, el backend lo avisa una vez en el log y sigue con la memoria en RAM
(la API y la voz funcionan igual). `MEMORY_ENABLED=false` la deja solo en RAM a propósito.

## Pruebas

`pi/tests/test_memory.py` cubre el diario y el resumen entre sesiones, las transiciones que observa, la
semilla, la poda, la carpeta no escribible y la API; `test_voice_live.py` cubre `recordar`/`apuntar` y el
prompt. Los tests nunca tocan `~/robert-memory`: `tests/conftest.py` da a cada uno su carpeta temporal.
