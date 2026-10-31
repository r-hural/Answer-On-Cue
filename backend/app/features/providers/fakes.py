"""Offline stand-ins for the provider protocols, for tests and keyless development."""

import io
import wave
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.features.providers.types import (
    AudioResult,
    Message,
    Provenance,
    ProviderError,
    Structured,
    T,
    Transcript,
    Usage,
    VoiceSpec,
)

FAKE_MODEL = "fake"


def _provenance(operation_id: str) -> Provenance:
    return Provenance(provider="openai", model=FAKE_MODEL, operation_id=operation_id)


class FakeTranscriber:
    def __init__(
        self, text: str = "To jest testowa odpowiedź.", error: ProviderError | None = None
    ) -> None:
        self.text = text
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def transcribe(
        self,
        audio: bytes,
        mime: str,
        glossary: list[str],
        *,
        operation_id: str,
        language: str,
    ) -> Transcript:
        self.calls.append(
            {
                "audio": audio,
                "mime": mime,
                "glossary": list(glossary),
                "operation_id": operation_id,
                "language": language,
            }
        )
        if self.error is not None:
            raise self.error
        return Transcript(
            text=self.text,
            language=language,
            usage=Usage(),
            provenance=_provenance(operation_id),
        )


class FakeTextGenerator:
    def __init__(
        self,
        responses: dict[str, BaseModel | Callable[[list[Message]], BaseModel]],
        error: ProviderError | None = None,
    ) -> None:
        self.responses = responses
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def generate_structured(
        self,
        task: str,
        system: str,
        messages: list[Message],
        schema: type[T],
        *,
        operation_id: str,
        max_output_tokens: int = 16000,
    ) -> Structured[T]:
        self.calls.append(
            {
                "task": task,
                "system": system,
                "messages": list(messages),
                "schema": schema,
                "operation_id": operation_id,
                "max_output_tokens": max_output_tokens,
            }
        )
        if self.error is not None:
            raise self.error
        response = self.responses[task]
        value = response(messages) if callable(response) else response
        return Structured[schema](
            value=schema.model_validate(value.model_dump()),
            usage=Usage(),
            provenance=_provenance(operation_id),
        )


def silent_wav(seconds: float = 0.5, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(seconds * rate))
    return buffer.getvalue()


class FakeSynthesizer:
    def __init__(self, error: ProviderError | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def synthesize(self, text: str, voice: VoiceSpec, *, operation_id: str) -> AudioResult:
        self.calls.append({"text": text, "voice": voice, "operation_id": operation_id})
        if self.error is not None:
            raise self.error
        return AudioResult(
            audio=silent_wav(),
            mime="audio/wav",
            usage=Usage(audio_seconds=0.5, characters=len(text)),
            provenance=Provenance(
                provider="azure_speech", model=FAKE_MODEL, operation_id=operation_id
            ),
        )
