import json

import httpx2
import pytest
from pydantic import BaseModel, SecretStr

from app.features.providers.anthropic_adapter import AnthropicTextGenerator
from app.features.providers.types import Message, ProviderError

KEY = SecretStr("sk-ant-test-0000000000abcd")
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class Answer(BaseModel):
    answer: str


class Recorder:
    def __init__(self, response: httpx2.Response) -> None:
        self.response = response
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.response

    def client(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self))

    def body(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


def message_body(
    text: str = '{"answer": "tak"}',
    *,
    stop_reason: str = "end_turn",
    model: str = "claude-sonnet-5-5",
    iterations: list[dict] | None = None,
    stop_details: dict | None = None,
) -> dict:
    usage: dict = {"input_tokens": 21, "output_tokens": 9}
    if iterations is not None:
        usage["iterations"] = iterations
    body = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage,
    }
    if stop_details is not None:
        body["stop_details"] = stop_details
    return body


def ok(body: dict | None = None) -> httpx2.Response:
    return httpx2.Response(200, json=body or message_body(), headers={"request-id": "req_a1"})


def generate(recorder: Recorder, *, fallbacks: bool = True):
    generator = AnthropicTextGenerator(
        KEY, "claude-sonnet-5-5", fallbacks=fallbacks, http_client=recorder.client()
    )
    return generator.generate_structured(
        "t",
        "Jesteś egzaminatorem.",
        [Message(role="user", content="Pytanie?")],
        Answer,
        operation_id="op-9",
        max_output_tokens=2000,
    )


def test_parse_returns_value_and_usage():
    recorder = Recorder(ok())
    result = generate(recorder)
    assert result.value == Answer(answer="tak")
    assert result.usage.input_tokens == 21
    assert result.usage.output_tokens == 9
    assert result.provenance.provider == "anthropic"
    assert result.provenance.model == "claude-sonnet-5-5"
    assert result.provenance.request_id == "req_a1"
    assert result.provenance.served_by_fallback is False
    body = recorder.body()
    assert body["system"] == "Jesteś egzaminatorem."
    assert body["max_tokens"] == 2000
    assert body["messages"] == [{"role": "user", "content": "Pytanie?"}]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert recorder.requests[0].headers["idempotency-key"] == "op-9"


def test_fallbacks_requested_by_default():
    recorder = Recorder(ok())
    generate(recorder)
    assert FALLBACK_BETA in recorder.requests[0].headers["anthropic-beta"]
    assert recorder.body()["fallbacks"] == "default"


def test_fallbacks_off():
    recorder = Recorder(ok())
    generate(recorder, fallbacks=False)
    assert "anthropic-beta" not in recorder.requests[0].headers
    assert "fallbacks" not in recorder.body()


def test_served_by_fallback_flag():
    iterations = [
        {"type": "message", "input_tokens": 21, "output_tokens": 0},
        {"type": "fallback_message", "input_tokens": 21, "output_tokens": 9},
    ]
    recorder = Recorder(ok(message_body(model="claude-opus-5-5", iterations=iterations)))
    result = generate(recorder)
    assert result.provenance.served_by_fallback is True
    assert result.provenance.model == "claude-opus-5-5"


def test_refusal_stop_reason():
    body = message_body(
        "", stop_reason="refusal", stop_details={"type": "refusal", "category": "cyber"}
    )
    with pytest.raises(ProviderError) as caught:
        generate(Recorder(ok(body)))
    assert caught.value.kind == "refused"
    assert "cyber" in str(caught.value)


def test_max_tokens_invalid_output():
    with pytest.raises(ProviderError) as caught:
        generate(Recorder(ok(message_body('{"answer": "ta', stop_reason="max_tokens"))))
    assert caught.value.kind == "invalid_output"
    assert str(caught.value) == "The model's answer was cut off."


def test_invalid_json_invalid_output():
    with pytest.raises(ProviderError) as caught:
        generate(Recorder(ok(message_body('{"other": 1}'))))
    assert caught.value.kind == "invalid_output"


def test_no_sampling_params_sent():
    recorder = Recorder(ok())
    generate(recorder)
    body = recorder.body()
    for name in ("temperature", "top_p", "top_k", "thinking"):
        assert name not in body
    assert all(m["role"] == "user" for m in body["messages"])


def test_429_retryable():
    response = httpx2.Response(
        429,
        json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}},
        headers={"retry-after-ms": "1"},
    )
    recorder = Recorder(response)
    with pytest.raises(ProviderError) as caught:
        generate(recorder)
    assert caught.value.kind == "rate_limit"
    assert caught.value.retryable is True
    assert len(recorder.requests) == 3


def test_401_auth():
    response = httpx2.Response(
        401,
        json={"type": "error", "error": {"type": "authentication_error", "message": "bad key"}},
    )
    with pytest.raises(ProviderError) as caught:
        generate(Recorder(response))
    assert caught.value.kind == "auth"
    assert caught.value.retryable is False


def test_client_limits():
    generator = AnthropicTextGenerator(KEY, "claude-sonnet-5-5")
    assert generator.client.max_retries == 2
    assert generator.client.timeout == 120.0
