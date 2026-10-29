"""The speech-to-speech AI behind Robert's voice, one adapter per provider.

Pick it in pi/.env with AI_PROVIDER (gemini or openai) and AI_API_KEY. Every
adapter turns its provider's live session into the same small interface, so
LiveConversation never knows who it is talking to:

    async with provider.connect(instruction) as link:
        await link.say(text)               Robert speaks first (social greeting)
        await link.send_audio(frame)       16 kHz int16 microphone frames
        async for kind, value in link.events():
            "heard" str | "said" str        transcripts, in pieces
            "audio" bytes                   24 kHz PCM16 to play
            "tools" [ToolCall]              function calls to run
            "turn_done" None                the model finished its turn
            "close" None                    the server ended the session
        await link.answer([(call, result_dict, jpeg_or_None), ...])

Another provider = one more module with a class like these and an entry in
PROVIDERS. It needs a live audio model with function calling (and images in
tool results, for the camera).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict


def _gemini():
    from .gemini import Gemini
    return Gemini


def _openai():
    from .openai import OpenAI
    return OpenAI


# AI_PROVIDER value -> adapter class (imported lazily: each one pulls its own SDK).
PROVIDERS = {"gemini": _gemini, "google": _gemini, "openai": _openai, "chatgpt": _openai}


def make(name: str, api_key: str, model: str = "", voice: str = "", silence_ms: int = 600):
    try:
        cls = PROVIDERS[name.strip().lower()]()
    except KeyError:
        raise ValueError(f"AI_PROVIDER={name!r}: use one of {', '.join(sorted(PROVIDERS))}") from None
    return cls(api_key, model or cls.default_model, voice or cls.default_voice, silence_ms)
