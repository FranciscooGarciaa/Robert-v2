"""Gemini Live (Google AI Studio key): one WebSocket session, audio both ways.

Measured on the Pi 2026-10-23: ~0.5 s to connect, 0.6-0.8 s from the end of
speech to the first audio of the reply.
"""

from contextlib import asynccontextmanager

from ..audio import MIC_RATE
from . import ToolCall


def session_config(instruction: str, voice: str, silence_ms: int, tools: list[dict]):
    from google.genai import types

    declarations = [types.FunctionDeclaration(name=t["name"], description=t["description"],
                                              parameters=t.get("parameters")) for t in tools]
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=instruction,
        tools=[types.Tool(function_declarations=declarations)],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                silence_duration_ms=silence_ms, prefix_padding_ms=100)),
    )


class Gemini:
    default_model = "gemini-3.8-live"
    default_voice = "Puck"

    def __init__(self, api_key: str, model: str, voice: str, silence_ms: int, client=None):
        if client is None:
            from google import genai

            client = genai.Client(api_key=api_key)
        self._client = client
        self.model, self.voice, self.silence_ms = model, voice, silence_ms

    @asynccontextmanager
    async def connect(self, instruction: str):
        from ..live import TOOLS

        config = session_config(instruction, self.voice, self.silence_ms, TOOLS)
        async with self._client.aio.live.connect(model=self.model, config=config) as session:
            yield GeminiLink(session)

    @staticmethod
    def out_of_credit(exc: Exception) -> bool:
        return "credits are depleted" in str(exc)


class GeminiLink:
    def __init__(self, session):
        self._s = session

    async def say(self, text: str) -> None:
        from google.genai import types

        await self._s.send_client_content(
            turns=types.Content(role="user", parts=[types.Part.from_text(text=text)]), turn_complete=True)

    async def send_audio(self, frame) -> None:
        from google.genai import types

        await self._s.send_realtime_input(
            audio=types.Blob(data=frame.tobytes(), mime_type=f"audio/pcm;rate={MIC_RATE}"))

    async def events(self):
        while True:  # receive() yields one model turn, then returns
            async for msg in self._s.receive():
                if msg.go_away:
                    yield "close", None
                    return
                if msg.tool_call:
                    yield "tools", [ToolCall(c.id, c.name, dict(c.args or {}))
                                    for c in msg.tool_call.function_calls]
                sc = msg.server_content
                if not sc:
                    continue
                if sc.input_transcription and sc.input_transcription.text:
                    yield "heard", sc.input_transcription.text
                if sc.output_transcription and sc.output_transcription.text:
                    yield "said", sc.output_transcription.text
                if sc.model_turn:
                    for part in sc.model_turn.parts or []:
                        if part.inline_data and part.inline_data.data:
                            yield "audio", part.inline_data.data
                if sc.turn_complete:
                    yield "turn_done", None

    async def answer(self, results) -> None:
        from google.genai import types

        responses = []
        for call, result, jpeg in results:
            parts = None
            if jpeg is not None:  # a photo goes back with the call it answers
                parts = [types.FunctionResponsePart(inline_data=types.FunctionResponseBlob(
                    mime_type="image/jpeg", data=jpeg))]
            responses.append(types.FunctionResponse(id=call.id, name=call.name, response=result, parts=parts))
        await self._s.send_tool_response(function_responses=responses)
