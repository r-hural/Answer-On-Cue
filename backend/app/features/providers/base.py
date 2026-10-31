from typing import Protocol, runtime_checkable

from app.features.providers.types import (
    AudioResult,
    Message,
    Structured,
    T,
    Transcript,
    VoiceSpec,
)


@runtime_checkable
class Transcriber(Protocol):
    def transcribe(
        self,
        audio: bytes,
        mime: str,
        glossary: list[str],
        *,
        operation_id: str,
        language: str,
    ) -> Transcript: ...


@runtime_checkable
class TextGenerator(Protocol):
    def generate_structured(
        self,
        task: str,
        system: str,
        messages: list[Message],
        schema: type[T],
        *,
        operation_id: str,
        max_output_tokens: int = 16000,
    ) -> Structured[T]: ...


@runtime_checkable
class Synthesizer(Protocol):
    """Implemented by the voice module."""

    def synthesize(self, text: str, voice: VoiceSpec, *, operation_id: str) -> AudioResult: ...
