import logging
import traceback

import httpx2
import pytest
from pydantic import BaseModel, SecretStr

from app.config import Settings
from app.features.providers import router as providers_router
from app.features.providers.anthropic_adapter import AnthropicTextGenerator
from app.features.providers.openai_adapter import OpenAITextGenerator, OpenAITranscriber
from app.features.providers.types import REDACTED, Message, ProviderError, redact
from app.main import create_app
from tests.conftest import local_client

LEAK = "sk-test-LEAKCHECK-9f3a"
KEY_ENV = ("AOC_OPENAI_API_KEY", "AOC_ANTHROPIC_API_KEY", "AOC_AZURE_SPEECH_KEY")


class Answer(BaseModel):
    answer: str


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in KEY_ENV:
        monkeypatch.delenv(key, raising=False)


def echo_401(request: httpx2.Request) -> httpx2.Response:
    """A hostile 401 that echoes the key back in the body and a header."""
    message = f"Incorrect API key provided: {LEAK}. Authorization: Bearer {LEAK}"
    return httpx2.Response(
        401,
        json={"error": {"message": message, "type": "authentication_error"}},
        headers={"x-echo": LEAK},
    )


def _client(handler) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(handler))


def _all_text(error: BaseException) -> str:
    return "\n".join(
        [str(error), repr(error), *map(str, error.args), "".join(traceback.format_exception(error))]
    )


def _call_adapters(handler) -> list[ProviderError]:
    key = SecretStr(LEAK)
    messages = [Message(role="user", content="x")]
    calls = [
        lambda: OpenAITextGenerator(key, "m", http_client=_client(handler)).generate_structured(
            "t", "s", messages, Answer, operation_id="op"
        ),
        lambda: AnthropicTextGenerator(key, "m", http_client=_client(handler)).generate_structured(
            "t", "s", messages, Answer, operation_id="op"
        ),
        lambda: OpenAITranscriber(key, "m", http_client=_client(handler)).transcribe(
            b"RIFF", "audio/wav", ["termin"], operation_id="op", language="pl"
        ),
    ]
    errors = []
    for call in calls:
        with pytest.raises(ProviderError) as caught:
            call()
        errors.append(caught.value)
    return errors


def test_redact_patterns():
    assert LEAK not in redact(f"key={LEAK}")
    assert redact("Bearer abc.def") == f"Bearer {REDACTED}"
    assert "sk-ant-api03-abcdefghijk" not in redact("x sk-ant-api03-abcdefghijk y")


def test_no_key_in_error_messages():
    errors = _call_adapters(echo_401)
    assert [e.kind for e in errors] == ["auth", "auth", "auth"]
    for error in errors:
        text = _all_text(error)
        assert LEAK not in text
        assert REDACTED in str(error)


def test_unregistered_key_shape_redacted():
    error = ProviderError("auth", "openai", "bad key sk-proj-ABCDEFGH12345678")
    assert "sk-proj-ABCDEFGH12345678" not in str(error)


def test_no_key_in_logs(caplog):
    caplog.set_level(logging.DEBUG)
    for name in ("openai", "anthropic", "httpx", "httpx2", "httpcore", "app"):
        caplog.set_level(logging.DEBUG, logger=name)

    def ok_then_echo(request: httpx2.Request) -> httpx2.Response:
        return echo_401(request)

    _call_adapters(ok_then_echo)
    logged = "\n".join(f"{r.getMessage()} {r.args!r} {r.exc_text or ''}" for r in caplog.records)
    assert caplog.records, "SDK debug logging should have produced records"
    assert LEAK not in logged


def test_no_key_in_any_response(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    settings = Settings(
        _env_file=env_path,
        data_dir=tmp_path / "data",
        frontend_dist=tmp_path / "dist",
        env_file_path=env_path,
        base_env_file=None,
    )
    monkeypatch.setattr(providers_router, "http_client_factory", lambda: _client(echo_401))
    client = local_client(create_app(settings))
    responses = [
        client.put("/api/providers/keys/openai", json={"value": LEAK}),
        client.put("/api/providers/keys/anthropic", json={"value": LEAK}),
        client.put("/api/providers/keys/azure_speech", json={"value": LEAK}),
        client.put("/api/providers/keys/azure_speech", json={"value": LEAK, "region": "x y"}),
        client.put("/api/providers/keys/openai", json={"value": f"{LEAK} extra"}),
        client.put("/api/providers/keys/unknown", json={"value": LEAK}),
        client.put("/api/providers/keys/openai", json={"value": [LEAK]}),
        client.put("/api/providers/keys/openai", json=LEAK),
        client.put(
            "/api/providers/keys/openai",
            content=f'{{"value": "{LEAK}',
            headers={"content-type": "application/json"},
        ),
        client.get("/api/providers/status"),
        client.post("/api/providers/keys/openai/test"),
        client.post("/api/providers/keys/anthropic/test"),
        client.post("/api/providers/keys/azure_speech/test"),
        client.delete("/api/providers/keys/anthropic"),
        client.get("/api/providers/status"),
    ]
    assert responses[0].status_code == 200
    assert responses[10].json()["error_kind"] == "auth"
    for response in responses:
        assert LEAK not in response.text, response.request.url
        assert all(LEAK not in v for v in response.headers.values())
    assert LEAK in env_path.read_text(encoding="utf-8")


def test_settings_repr_hides_keys():
    settings = Settings(
        _env_file=None,
        openai_api_key=LEAK,
        anthropic_api_key=LEAK,
        azure_speech_key=LEAK,
    )
    for text in (repr(settings), str(settings), settings.model_dump_json()):
        assert LEAK not in text
    assert LEAK not in repr(SecretStr(LEAK))


def test_unshaped_key_redacted_across_truncation():
    import openai

    from app.features.providers.errors import map_sdk_error
    from app.features.providers.types import register_secret

    secret = "AZURE0LEAK0KEY0WITHOUT0PREFIX0123456789"
    register_secret(secret)
    request = httpx2.Request("POST", "https://api.openai.com/v1/x")
    response = httpx2.Response(401, request=request)
    exc = openai.AuthenticationError("x" * 290 + secret, response=response, body=None)
    text = str(map_sdk_error(openai, "openai", exc))
    assert secret[:8] not in text
