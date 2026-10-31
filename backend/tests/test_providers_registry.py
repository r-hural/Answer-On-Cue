import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel, SecretStr

from app.config import Settings
from app.features.providers.anthropic_adapter import AnthropicTextGenerator
from app.features.providers.fakes import FakeTextGenerator, FakeTranscriber
from app.features.providers.registry import (
    GenerationDep,
    RecordingTextGenerator,
    RecordingTranscriber,
    get_text_generator,
    get_transcriber,
)
from app.features.providers.types import Message, ProviderError
from app.features.providers.usage import InMemoryUsageRecorder
from app.main import create_app

MISSING_OPENAI = "No API key for: openai. Add it in Settings."


class Answer(BaseModel):
    answer: str


@pytest.fixture
def keyless(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        frontend_dist=tmp_path / "dist",
        env_file_path=tmp_path / ".env",
        openai_api_key=None,
        anthropic_api_key=None,
    )


def test_missing_key_unavailable(keyless):
    with pytest.raises(ProviderError) as caught:
        get_text_generator(keyless, "generation")
    assert caught.value.kind == "unavailable"
    assert caught.value.provider == "openai"
    assert str(caught.value) == MISSING_OPENAI


def test_missing_key_transcriber_unavailable(keyless):
    with pytest.raises(ProviderError) as caught:
        get_transcriber(keyless)
    assert caught.value.kind == "unavailable"
    assert str(caught.value) == MISSING_OPENAI


def test_app_starts_without_keys(keyless):
    client = TestClient(create_app(keyless))
    assert client.get("/api/health").status_code == 200


def test_dependency_without_key_is_503(keyless):
    app = create_app(keyless)
    router = APIRouter()

    @router.get("/probe")
    def probe(generator: GenerationDep) -> dict[str, str]:
        return {"ok": "yes"}

    app.include_router(router, prefix="/test")
    response = TestClient(app).get("/test/probe")
    assert response.status_code == 503
    assert response.json()["detail"] == MISSING_OPENAI


def test_role_selects_anthropic_default_model(keyless):
    settings = keyless.model_copy(
        update={
            "generation_provider": "anthropic",
            "anthropic_api_key": SecretStr("sk-ant-test-0000000000abcd"),
        }
    )
    generator = get_text_generator(settings, "generation")
    assert generator.provider == "anthropic"
    assert generator.model == "claude-sonnet-5-5"
    assert isinstance(generator.inner, AnthropicTextGenerator)
    assert generator.inner.model == "claude-sonnet-5-5"
    assert generator.inner.fallbacks is True


def test_role_keeps_explicit_anthropic_model(keyless):
    settings = keyless.model_copy(
        update={
            "assessment_provider": "anthropic",
            "assessment_model": "claude-opus-5-5",
            "anthropic_api_key": SecretStr("sk-ant-test-0000000000abcd"),
        }
    )
    generator = get_text_generator(settings, "assessment")
    assert generator.model == "claude-opus-5-5"


def test_recording_wrapper_records_success_and_error():
    recorder = InMemoryUsageRecorder()
    fake = FakeTextGenerator({"t": Answer(answer="ok")})
    wrapped = RecordingTextGenerator(lambda: fake, "openai", "m", "generation", recorder)
    messages = [Message(role="user", content="hej")]
    wrapped.generate_structured("t", "sys", messages, Answer, operation_id="op-1")
    fake.error = ProviderError("timeout", "openai", "Timed out.", retryable=True)
    with pytest.raises(ProviderError):
        wrapped.generate_structured("t", "sys", messages, Answer, operation_id="op-2")
    assert [e.status for e in recorder.events] == ["ok", "error"]
    assert recorder.events[0].error_kind is None
    assert recorder.events[1].error_kind == "timeout"
    assert [e.operation_id for e in recorder.events] == ["op-1", "op-2"]
    assert all(e.task == "t" and e.role == "generation" for e in recorder.events)


def test_recording_transcriber_records():
    recorder = InMemoryUsageRecorder()
    wrapped = RecordingTranscriber(lambda: FakeTranscriber(), "openai", "m", recorder)
    wrapped.transcribe(b"x", "audio/webm", [], operation_id="op-1", language="pl")
    assert recorder.events[0].task == "transcription"
    assert recorder.events[0].status == "ok"


def test_recording_wrapper_records_unexpected_error():
    recorder = InMemoryUsageRecorder()

    class Broken:
        def generate_structured(self, *args, **kwargs):
            raise UnicodeEncodeError("ascii", "ż", 0, 1, "bad")

    wrapped = RecordingTextGenerator(lambda: Broken(), "openai", "m", "generation", recorder)
    with pytest.raises(UnicodeEncodeError):
        wrapped.generate_structured(
            "t", "s", [Message(role="user", content="x")], Answer, operation_id="op"
        )
    assert recorder.events[0].status == "error"
    assert recorder.events[0].error_kind == "UnicodeEncodeError"
