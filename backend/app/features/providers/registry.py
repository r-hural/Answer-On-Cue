"""Settings → provider adapters, wrapped so every call is recorded as a usage event."""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal, TypeVar

from fastapi import Depends, HTTPException
from pydantic import SecretStr

from app.config import Settings, get_settings
from app.features.costs import estimates
from app.features.costs.spending import SpendingGuard, guard_for
from app.features.providers.base import TextGenerator, Transcriber
from app.features.providers.types import (
    AudioResult,
    Message,
    ProviderError,
    ProviderName,
    Structured,
    T,
    Transcript,
    Usage,
    VoiceSpec,
    register_secret,
)
from app.features.providers.usage import (
    UsageEvent,
    UsageRecorder,
    get_durable_usage_recorder,
)

Role = Literal["transcription", "generation", "assessment"]
R = TypeVar("R")


def missing_key_message(provider: ProviderName) -> str:
    return f"No API key for: {provider}. Add it in Settings."


def _require_key(settings: Settings, provider: ProviderName) -> SecretStr:
    key = {
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
        "azure_speech": settings.azure_speech_key,
    }[provider]
    if key is None or not key.get_secret_value().strip():
        raise ProviderError("unavailable", provider, missing_key_message(provider))
    register_secret(key.get_secret_value())
    return key


def _error_kind(error: Exception) -> str:
    return error.kind if isinstance(error, ProviderError) else type(error).__name__


class _Recording:
    def __init__(
        self,
        factory: Callable[[], object],
        provider: ProviderName,
        model: str,
        role: str,
        recorder: UsageRecorder | None,
        *,
        guard: SpendingGuard | None = None,
    ) -> None:
        self._factory = factory
        self._inner: object | None = None
        self.provider = provider
        self.model = model
        self.role = role
        self.recorder = recorder
        self.guard = guard

    @property
    def inner(self):
        # Built on first use, so no SDK client exists until a call is made.
        if self._inner is None:
            self._inner = self._factory()
        return self._inner

    def _record(
        self,
        operation_id: str,
        task: str,
        started_at: datetime,
        start: float,
        usage: Usage,
        model: str,
        error: Exception | None,
        request_id: str | None = None,
        estimated_usd: float | None = None,
        status: Literal["ok", "error", "blocked"] | None = None,
    ) -> None:
        if self.recorder is None:
            return
        self.recorder.record(
            UsageEvent(
                operation_id=operation_id,
                task=task,
                role=self.role,
                provider=self.provider,
                model=model,
                usage=usage,
                status=status or ("ok" if error is None else "error"),
                error_kind=None if error is None else _error_kind(error),
                started_at=started_at,
                duration_ms=int((time.perf_counter() - start) * 1000),
                request_id=request_id,
                estimated_usd=estimated_usd,
            )
        )

    def _call(self, operation_id: str, task: str, estimated_usd: float, call: Callable[[], R]) -> R:
        """One provider call, recorded with its estimate; the result has usage and provenance.

        With a ceiling the call first reserves its estimate: a blocked call is recorded as
        "blocked" and raises, never retried; the reservation is released after the row is written.
        """
        if self.guard is None:
            return self._run(operation_id, task, estimated_usd, call)
        started_at, start = datetime.now(UTC), time.perf_counter()
        try:
            reservation = self.guard.reserve(estimated_usd, self.provider)
        except ProviderError as error:
            self._record(
                operation_id,
                task,
                started_at,
                start,
                Usage(),
                self.model,
                error,
                None,
                estimated_usd,
                status="blocked",
            )
            raise
        with reservation:
            return self._run(operation_id, task, estimated_usd, call)

    def _run(self, operation_id: str, task: str, estimated_usd: float, call: Callable[[], R]) -> R:
        started_at, start = datetime.now(UTC), time.perf_counter()
        try:
            result = call()
        except Exception as error:
            self._record(
                operation_id,
                task,
                started_at,
                start,
                Usage(),
                self.model,
                error,
                None,
                estimated_usd,
            )
            raise
        self._record(
            operation_id,
            task,
            started_at,
            start,
            result.usage,
            result.provenance.model,
            None,
            result.provenance.request_id,
            estimated_usd,
        )
        return result


class RecordingTranscriber(_Recording):
    def __init__(
        self,
        factory: Callable[[], Transcriber],
        provider: ProviderName,
        model: str,
        recorder: UsageRecorder | None,
        *,
        guard: SpendingGuard | None = None,
    ) -> None:
        super().__init__(factory, provider, model, "transcription", recorder, guard=guard)

    def transcribe(
        self,
        audio: bytes,
        mime: str,
        glossary: list[str],
        *,
        operation_id: str,
        language: str,
    ) -> Transcript:
        seconds = estimates.audio_seconds(audio, mime)
        return self._call(
            operation_id,
            "transcription",
            estimates.transcription_usd(self.provider, self.model, seconds),
            lambda: self.inner.transcribe(
                audio, mime, glossary, operation_id=operation_id, language=language
            ),
        )


class RecordingTextGenerator(_Recording):
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
        chars_in = len(system) + sum(len(m.content) for m in messages)
        estimate = estimates.text_call_usd(
            self.provider,
            self.model,
            self.role,
            chars_in,
            min(estimates.typical_output(task), max_output_tokens),
        )
        return self._call(
            operation_id,
            task,
            estimate,
            lambda: self.inner.generate_structured(
                task,
                system,
                messages,
                schema,
                operation_id=operation_id,
                max_output_tokens=max_output_tokens,
            ),
        )


class RecordingSynthesizer(_Recording):
    """An Azure voice engine whose calls are recorded (Piper and the browser voice are free)."""

    def __init__(
        self,
        engine,
        voice: str,
        recorder: UsageRecorder | None,
        *,
        guard: SpendingGuard | None = None,
    ) -> None:
        super().__init__(lambda: engine, "azure_speech", voice, "voice", recorder, guard=guard)

    def engine_version(self, voice: str) -> str:
        return self.inner.engine_version(voice)

    def synthesize(self, text: str, voice: VoiceSpec, *, operation_id: str) -> AudioResult:
        return self._call(
            operation_id,
            "voice.synthesize",
            estimates.voice_usd(self.provider, voice.voice, len(text)),
            lambda: self.inner.synthesize(text, voice, operation_id=operation_id),
        )


def get_transcriber(settings: Settings, recorder: UsageRecorder | None = None) -> Transcriber:
    provider = settings.transcription_provider
    model = settings.transcription_model
    key = _require_key(settings, provider)

    def build() -> Transcriber:
        from app.features.providers.openai_adapter import OpenAITranscriber

        return OpenAITranscriber(key, model)

    return RecordingTranscriber(build, provider, model, recorder, guard=guard_for(settings))


def resolve_role(settings: Settings, role: Role) -> tuple[ProviderName, str]:
    """The provider and the model a role actually uses."""
    provider: ProviderName = getattr(settings, f"{role}_provider")
    model: str = getattr(settings, f"{role}_model")
    if provider == "anthropic" and model == Settings.model_fields[f"{role}_model"].default:
        model = settings.anthropic_default_model
    return provider, model


def get_text_generator(
    settings: Settings,
    role: Literal["generation", "assessment"],
    recorder: UsageRecorder | None = None,
) -> TextGenerator:
    provider, model = resolve_role(settings, role)
    key = _require_key(settings, provider)

    if provider == "anthropic":
        fallbacks = settings.anthropic_fallbacks

        def build() -> TextGenerator:
            from app.features.providers.anthropic_adapter import AnthropicTextGenerator

            return AnthropicTextGenerator(key, model, fallbacks=fallbacks)

    else:

        def build() -> TextGenerator:
            from app.features.providers.openai_adapter import OpenAITextGenerator

            return OpenAITextGenerator(key, model)

    return RecordingTextGenerator(build, provider, model, role, recorder, guard=guard_for(settings))


def _unavailable_as_http(error: ProviderError) -> HTTPException:
    return HTTPException(status_code=503, detail=str(error))


def _transcriber_dep(
    settings: Annotated[Settings, Depends(get_settings)],
    recorder: Annotated[UsageRecorder, Depends(get_durable_usage_recorder)],
) -> Transcriber:
    try:
        return get_transcriber(settings, recorder)
    except ProviderError as error:
        raise _unavailable_as_http(error) from None


def _generation_dep(
    settings: Annotated[Settings, Depends(get_settings)],
    recorder: Annotated[UsageRecorder, Depends(get_durable_usage_recorder)],
) -> TextGenerator:
    try:
        return get_text_generator(settings, "generation", recorder)
    except ProviderError as error:
        raise _unavailable_as_http(error) from None


def _assessment_dep(
    settings: Annotated[Settings, Depends(get_settings)],
    recorder: Annotated[UsageRecorder, Depends(get_durable_usage_recorder)],
) -> TextGenerator:
    try:
        return get_text_generator(settings, "assessment", recorder)
    except ProviderError as error:
        raise _unavailable_as_http(error) from None


TranscriberDep = Annotated[Transcriber, Depends(_transcriber_dep)]
GenerationDep = Annotated[TextGenerator, Depends(_generation_dep)]
AssessmentDep = Annotated[TextGenerator, Depends(_assessment_dep)]
