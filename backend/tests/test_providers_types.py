import io
import wave
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from app.features.providers.base import Synthesizer, TextGenerator, Transcriber
from app.features.providers.fakes import FakeSynthesizer, FakeTextGenerator, FakeTranscriber
from app.features.providers.types import (
    Message,
    Provenance,
    ProviderError,
    Structured,
    Usage,
    VoiceSpec,
)
from app.features.providers.usage import (
    InMemoryUsageRecorder,
    UsageEvent,
    UsageRecorder,
    get_usage_recorder,
)


class MySchema(BaseModel):
    answer: str


def _provenance() -> Provenance:
    return Provenance(provider="openai", model="m", operation_id="op-1")


def test_provider_error_fields():
    error = ProviderError(
        "rate_limit", "openai", "Za dużo zapytań.", retryable=True, status_code=429
    )
    assert error.kind == "rate_limit"
    assert error.provider == "openai"
    assert error.retryable is True
    assert error.status_code == 429
    assert str(error) == "Za dużo zapytań."
    assert isinstance(error, Exception)
    plain = ProviderError("auth", "anthropic", "Zły klucz.")
    assert plain.retryable is False
    assert plain.status_code is None


def test_provider_error_message_redacted():
    error = ProviderError("auth", "openai", "Invalid key sk-test-0000000000abcd given")
    assert "sk-test-0000000000abcd" not in str(error)


def test_structured_generic_holds_model():
    ok = Structured[MySchema](value={"answer": "tak"}, usage=Usage(), provenance=_provenance())
    assert isinstance(ok.value, MySchema)
    assert ok.value.answer == "tak"
    with pytest.raises(ValidationError):
        Structured[MySchema](value={"wrong": 1}, usage=Usage(), provenance=_provenance())


def test_fakes_satisfy_protocols():
    assert isinstance(FakeTranscriber(), Transcriber)
    assert isinstance(FakeTextGenerator({}), TextGenerator)
    assert isinstance(FakeSynthesizer(), Synthesizer)


def test_fake_transcriber_returns_text_and_records_call():
    fake = FakeTranscriber()
    result = fake.transcribe(b"x", "audio/webm", ["mitoza"], operation_id="op-1", language="pl")
    assert result.text == "To jest testowa odpowiedź."
    assert result.language == "pl"
    assert result.provenance.operation_id == "op-1"
    assert fake.calls[0]["glossary"] == ["mitoza"]


def test_fake_text_generator_by_task_and_error():
    fake = FakeTextGenerator(
        {
            "a": MySchema(answer="stałe"),
            "b": lambda messages: MySchema(answer=messages[-1].content.upper()),
        }
    )
    messages = [Message(role="user", content="echo")]
    first = fake.generate_structured("a", "sys", messages, MySchema, operation_id="op-1")
    second = fake.generate_structured("b", "sys", messages, MySchema, operation_id="op-2")
    assert first.value.answer == "stałe"
    assert second.value.answer == "ECHO"
    assert [c["task"] for c in fake.calls] == ["a", "b"]

    error = ProviderError("timeout", "openai", "Timed out.", retryable=True)
    failing = FakeTextGenerator({"a": MySchema(answer="x")}, error=error)
    with pytest.raises(ProviderError) as caught:
        failing.generate_structured("a", "sys", messages, MySchema, operation_id="op-3")
    assert caught.value is error
    assert len(failing.calls) == 1


def test_fake_synthesizer_valid_wav():
    fake = FakeSynthesizer()
    result = fake.synthesize("Dzień dobry", VoiceSpec(voice="pl-PL-test"), operation_id="op-1")
    assert result.mime == "audio/wav"
    with wave.open(io.BytesIO(result.audio)) as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
        assert wav.getnframes() / wav.getframerate() == pytest.approx(0.5)
    assert fake.calls[0]["text"] == "Dzień dobry"


def test_in_memory_recorder():
    recorder = InMemoryUsageRecorder()
    assert isinstance(recorder, UsageRecorder)
    event = UsageEvent(
        operation_id="op-1",
        task="t",
        role="generation",
        provider="openai",
        model="m",
        usage=Usage(input_tokens=3, output_tokens=4),
        status="ok",
        error_kind=None,
        started_at=datetime.now(UTC),
        duration_ms=12,
    )
    recorder.record(event)
    assert recorder.events == [event]
    assert get_usage_recorder() is get_usage_recorder()
    assert isinstance(get_usage_recorder(), InMemoryUsageRecorder)
