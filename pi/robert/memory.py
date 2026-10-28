"""What Robert remembers: a diary of each session and a summary carried across them.

Two plain files under MEMORY_DIR (default ~/robert-memory), both JSON:

    sessions/<id>.jsonl   one event per line for one boot of the backend (the diary)
    memory.json           what carries over: totals, the last sessions, the stories of
                          his days out, notes people asked Robert to keep, and his most
                          recent conversations

A seed file in the repo (pi/memory_seed.json) holds the stories Robert starts
with; any story in it that memory.json does not have yet is added on boot.

Events come from two places: the backend records what it is told (voice turns,
spoken commands, notes, photos, greetings) and `observe()` watches the shared
RobotState every tick and writes down the transitions worth remembering
(ESP32 lost, a safety stop, follow-me starting and why it ended, explore,
getting stuck, a conversation starting). `recall()` turns the summary into a
few lines of Spanish that the voice puts in Robert's prompt, so he knows what
he has lived through so far and can be asked about it.

Writing never raises: with the SD protected (read-only overlay) the files
live in RAM like the photos, and if even that fails the memory just stays in
memory for the session.
"""

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .robot.state import RobotState

log = logging.getLogger("robert.memory")

SCHEMA = 1
KEEP_SESSIONS = 30        # session diaries kept on disk, and sessions listed in the summary
KEEP_TALKS = 30           # recent conversation turns kept in the summary
KEEP_NOTES = 100
KEEP_EVENTS = 50          # recent notable events kept in the summary
RECALL_TALKS = 5          # how much of it goes into the voice prompt
RECALL_NOTES = 10
RECALL_STORIES = 10
KEEP_STORIES = 100
MAX_TEXT = 300            # longest heard/said/note kept, in characters

# Event kinds that add up in the summary totals ("talk" counts turns; "conversation" counts talks).
COUNTED = ("conversation", "talk", "photo", "dance", "follow", "explore", "greeting", "note",
           "story", "failsafe", "esp32_lost", "stuck")
# Kinds too frequent or too dull for the summary's recent events (still in the diary).
QUIET = ("talk", "command", "boot", "shutdown", "esp32_connected", "conversation_end")

LABELS = {  # singular, plural
    "conversation": ("conversación", "conversaciones"), "photo": ("foto", "fotos"),
    "dance": ("baile", "bailes"), "follow": ("vez siguiendo a alguien", "veces siguiendo a alguien"),
    "explore": ("vez explorando", "veces explorando"),
    "greeting": ("saludo a un desconocido", "saludos a desconocidos"),
    "failsafe": ("parada de seguridad", "paradas de seguridad"),
    "esp32_lost": ("pérdida del controlador de motores", "pérdidas del controlador de motores"),
    "stuck": ("atasco", "atascos")}


def _n(kind: str, n: int) -> str:
    return f"{n} {LABELS[kind][0 if n == 1 else 1]}"


def _counts(counts: dict) -> str:
    return ", ".join(_n(k, v) for k, v in counts.items() if k in LABELS and v)


def _now_iso(clock: Callable[[], float]) -> str:
    return datetime.fromtimestamp(clock()).astimezone().isoformat(timespec="seconds")


def _short(text: str | None) -> str | None:
    if text is None:
        return None
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def _when(iso: str | None) -> str:
    """'2026-10-27T18:02:11+02:00' -> '2026-10-27 18:02' (for the prompt)."""
    return iso[:16].replace("T", " ") if iso else "?"


def _minutes(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    m = int(seconds // 60)
    return f"{m} min" if m < 120 else f"{m // 60} h {m % 60} min"


class Memory:
    def __init__(self, root: Path, enabled: bool = True, clock: Callable[[], float] = time.time,
                 seed: Path | None = None):
        self.root = Path(root)
        self.enabled = enabled
        self._clock = clock
        self._lock = threading.Lock()
        self._write_failed = False
        self._seen: dict = {}          # last values observed on the robot state
        self._talk_started: float | None = None
        self.session_id = datetime.fromtimestamp(clock()).strftime("%Y%m%d-%H%M%S")
        self.session_path = self.root / "sessions" / f"{self.session_id}.jsonl"
        self.summary_path = self.root / "memory.json"
        self.summary = self._load_summary()
        self.session = {"id": self.session_id, "started": _now_iso(clock), "ended": None,
                        "duration_s": None, "counts": {}}
        self.summary["sessions"] = (self.summary.get("sessions") or [])[-(KEEP_SESSIONS - 1):]
        self.summary["sessions"].append(self.session)
        self.summary["boots"] = int(self.summary.get("boots") or 0) + 1
        if not self.summary.get("first_boot"):
            self.summary["first_boot"] = self.session["started"]
        self._prune_diaries()
        self.record("boot")
        if seed:
            self._seed(seed)

    def _seed(self, path: Path) -> None:
        """Adds the stories from the seed file that the memory does not have yet."""
        try:
            stories = json.loads(Path(path).read_text(encoding="utf-8")).get("stories") or []
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("memory seed %s unreadable: %r", path, exc)
            return
        known = {x["text"] for x in self.summary["stories"]}
        for story in stories:
            text = _short(story.get("text")) if isinstance(story, dict) else None
            if text and text not in known:
                self.record("story", when=story.get("when"), text=text, seeded=True)

    # --- persistence ---------------------------------------------------------

    def _load_summary(self) -> dict:
        try:
            data = json.loads(self.summary_path.read_text())
            if isinstance(data, dict) and data.get("schema") == SCHEMA:
                for key, empty in (("totals", {}), ("sessions", []), ("stories", []), ("notes", []),
                                   ("recent_talks", []), ("recent_events", [])):
                    data.setdefault(key, empty)
                return data
            log.warning("memory.json has schema %r (want %d): starting over", data.get("schema")
                        if isinstance(data, dict) else None, SCHEMA)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("memory.json unreadable (%r): starting over", exc)
        return {"schema": SCHEMA, "first_boot": None, "boots": 0, "totals": {}, "sessions": [],
                "stories": [], "notes": [], "recent_talks": [], "recent_events": []}

    def _prune_diaries(self) -> None:
        try:
            diaries = sorted((self.root / "sessions").glob("*.jsonl"))
            for old in diaries[: max(0, len(diaries) - (KEEP_SESSIONS - 1))]:
                old.unlink()
        except OSError:
            pass

    def _write(self, event: dict) -> None:
        if not self.enabled:
            return
        try:
            self.session_path.parent.mkdir(parents=True, exist_ok=True)
            with self.session_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
            tmp = self.summary_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.summary, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.summary_path)
            self._write_failed = False
        except OSError as exc:
            if not self._write_failed:  # say it once, not on every event
                log.warning("cannot write the memory under %s (%r): keeping it in RAM", self.root, exc)
            self._write_failed = True

    # --- recording -----------------------------------------------------------

    def record(self, kind: str, **data) -> dict:
        """Writes one event to the diary and folds it into the summary. Never raises."""
        event = {"t": _now_iso(self._clock), "kind": kind}
        event.update({k: (_short(v) if isinstance(v, str) else v) for k, v in data.items() if v is not None})
        with self._lock:
            try:
                self._fold(event)
            except Exception:  # noqa: BLE001 - the memory must never break the robot
                log.exception("memory could not fold %r", event)
            self._write(event)
        return event

    def _fold(self, event: dict) -> None:
        kind, s = event["kind"], self.summary
        counts = self.session["counts"]
        if kind in COUNTED:
            counts[kind] = counts.get(kind, 0) + 1
            s["totals"][kind] = int(s["totals"].get(kind, 0)) + 1
        if kind == "talk":
            s["recent_talks"] = (s["recent_talks"] + [{"t": event["t"], "heard": event.get("heard"),
                                                       "reply": event.get("reply")}])[-KEEP_TALKS:]
        elif kind == "note":
            s["notes"] = (s["notes"] + [{"t": event["t"], "text": event.get("text", "")}])[-KEEP_NOTES:]
        elif kind == "story":
            s["stories"] = (s["stories"] + [{"t": event.get("when") or event["t"],
                                             "text": event.get("text", "")}])[-KEEP_STORIES:]
        elif kind == "shutdown":
            self.session["ended"] = event["t"]
            self.session["duration_s"] = event.get("uptime_s")
        if kind not in QUIET:
            s["recent_events"] = (s["recent_events"] + [event])[-KEEP_EVENTS:]

    def note(self, text: str) -> dict | None:
        text = _short(text)
        return self.record("note", text=text) if text else None

    def story(self, text: str, when: str | None = None) -> dict | None:
        """Something Robert lived through, told in his own voice (a day out, an adventure)."""
        text = _short(text)
        return self.record("story", when=when, text=text) if text else None

    def forget_note(self, index: int) -> bool:
        with self._lock:
            notes = self.summary["notes"]
            if not 0 <= index < len(notes):
                return False
            gone = notes.pop(index)
        self.record("note_forgotten", text=gone["text"])
        return True

    def close(self) -> None:
        self.record("shutdown", uptime_s=int(self._clock() - self._started))

    @property
    def _started(self) -> float:
        return datetime.fromisoformat(self.session["started"]).timestamp()

    # --- watching the robot --------------------------------------------------

    def observe(self, robot: RobotState) -> None:
        """Call every tick: records the transitions worth remembering."""
        seen = self._seen
        now = {
            "connected": robot.esp32.connected,
            "failsafe": robot.esp32.failsafe,
            "voice": robot.voice.state,
            "follow": (robot.nav.follow or {}).get("state"),
            "follow_reason": (robot.nav.follow or {}).get("reason"),
            "auto": robot.nav.auto,
            "stuck": robot.nav.stuck,
            "dancing": robot.nav.dancing,
        }
        if not seen:            # first look: nothing has changed yet
            self._seen = now
            return
        if now["connected"] != seen["connected"]:
            if now["connected"]:
                self.record("esp32_connected", reset_reason=robot.esp32.reset_reason)
            else:
                self.record("esp32_lost")
        if now["failsafe"] != seen["failsafe"] and now["failsafe"] not in (None, "none"):
            self.record("failsafe", failsafe=now["failsafe"],
                        distance_cm=robot.ultrasonic.distance_cm)
        busy = ("listening", "thinking", "speaking")
        if now["voice"] in busy and seen["voice"] not in busy:
            self._talk_started = self._clock()
            self.record("conversation")
        elif now["voice"] not in busy and seen["voice"] in busy:
            lasted = None if self._talk_started is None else int(self._clock() - self._talk_started)
            self._talk_started = None
            self.record("conversation_end", duration_s=lasted)
        if now["follow"] == "following" and seen["follow"] != "following":
            self.record("follow")
        elif now["follow"] == "stopped" and seen["follow"] in ("searching", "following", "lost"):
            self.record("follow_end", reason=now["follow_reason"])
        if now["auto"] == "explore" and seen["auto"] != "explore":
            self.record("explore")
        if now["stuck"] and not seen["stuck"]:
            self.record("stuck")
        if now["dancing"] and not seen["dancing"]:
            self.record("dance")
        self._seen = now

    # --- reading -------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.summary, ensure_ascii=False))

    def diary(self) -> list[dict]:
        """The current session's events, from its file (empty if it could not be written)."""
        try:
            return [json.loads(line) for line in self.session_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()]
        except (OSError, ValueError):
            return []

    def recall(self) -> str:
        """A few lines of Spanish for the voice prompt: what Robert remembers."""
        with self._lock:
            s, totals = self.summary, self.summary["totals"]
            times = "una vez" if s["boots"] == 1 else f"{s['boots']} veces"
            lines = [f"Tu memoria: te han encendido {times} desde el {_when(s['first_boot'])[:10]}."]
            summed = _counts({k: totals.get(k, 0) for k in LABELS})
            if summed:
                lines.append("En total: " + summed + ".")
            previous = [x for x in s["sessions"] if x["id"] != self.session_id]
            if previous:
                last = previous[-1]
                done = _counts(last["counts"])
                lines.append(f"Última sesión: {_when(last['started'])}, duró {_minutes(last.get('duration_s'))}"
                             + (f"; {done}" if done else "; no pasó gran cosa") + ".")
            done = _counts(self.session["counts"])
            lines.append(f"Esta sesión: llevas {_minutes(self._clock() - self._started)} encendido"
                         + (f"; {done}" if done else "") + ".")
            if s["stories"]:
                lines.append("Lo que has vivido (tus recuerdos, contados por ti):")
                # Oldest first: seeded memories of his first body can arrive after newer ones.
                stories = sorted(s["stories"], key=lambda x: x["t"])[-RECALL_STORIES:]
                lines += [f"- ({_when(x['t'])[:10]}) {x['text']}" for x in stories]
            if s["notes"]:
                lines.append("Cosas que te han pedido recordar:")
                lines += [f"- ({_when(n['t'])}) {n['text']}" for n in s["notes"][-RECALL_NOTES:]]
            talks = [t for t in s["recent_talks"] if t.get("heard") or t.get("reply")][-RECALL_TALKS:]
            if talks:
                lines.append("Últimas conversaciones:")
                for t in talks:
                    heard = f"te dijeron «{t['heard']}»" if t.get("heard") else "no entendiste nada"
                    said = f" y contestaste «{t['reply']}»" if t.get("reply") else ""
                    lines.append(f"- ({_when(t['t'])}) {heard}{said}.")
            return "\n".join(lines)
