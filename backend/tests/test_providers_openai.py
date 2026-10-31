import json

import httpx2
import pytest
from pydantic import BaseModel, SecretStr

from app.features.providers.openai_adapter import (
    OpenAITextGenerator,
    OpenAITranscriber,
    glossary_prompt,
)
from app.features.providers.types import Message, ProviderError

KEY = SecretStr("sk-test-0000000000abcd")
FAST_RETRY = {"retry-after-ms": "1"}


class Answer(BaseModel):
    answer: str


class Recorder:
    """MockTransport handler that records requests and replays canned responses."""

    def __init__(self, *responses: httpx2.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]

    def client(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self))


def response_body(content: list[dict], status: str = "completed") -> dict:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "model": "gpt-6.1-sol-2026-09-01",
        "status": status,
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": content,
            }
        ],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 11,
            "output_tokens": 7,
            "total_tokens": 18,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def text_item(text: str) -> dict:
    return {"type": "output_text", "text": text, "annotations": []}


def error_response(status: int, message: str = "error", headers: dict | None = None):
    body = {"error": {"message": message, "type": "error", "code": None, "param": None}}
    return httpx2.Response(status, json=body, headers=headers or {})


def generator(recorder: Recorder) -> OpenAITextGenerator:
    return OpenAITextGenerator(KEY, "gpt-6.1-sol", http_client=recorder.client())


def generate(recorder: Recorder):
    return generator(recorder).generate_structured(
        "t",
        "Jesteś egzaminatorem.",
        [Message(role="user", content="Pytanie?")],
        Answer,
        operation_id="op-123",
    )


def test_transcribe_sends_language_glossary_and_filename():
    recorder = Recorder(
        httpx2.Response(
            200,
            json={"text": "Mitoza to podział.", "usage": {"type": "duration", "seconds": 3.5}},
            headers={"x-request-id": "req_t1"},
        )
    )
    transcriber = OpenAITranscriber(KEY, "gpt-transcribe", http_client=recorder.client())
    result = transcriber.transcribe(
        b"\x1aE\xdf\xa3audio",
        "audio/webm",
        ["mitoza", "mejoza"],
        operation_id="op-1",
        language="pl",
    )
    request = recorder.requests[0]
    assert request.url.path.endswith("/audio/transcriptions")
    body = request.content.decode("utf-8", errors="replace")
    assert 'name="language"\r\n\r\npl\r\n' in body
    assert 'name="prompt"\r\n\r\nSłownik terminów: mitoza, mejoza\r\n' in body
    assert 'filename="answer.webm"' in body
    assert result.text == "Mitoza to podział."
    assert result.language == "pl"
    assert result.usage.audio_seconds == 3.5
    assert result.provenance.provider == "openai"
    assert result.provenance.model == "gpt-transcribe"
    assert result.provenance.operation_id == "op-1"
    assert result.provenance.request_id == "req_t1"


def test_transcribe_empty_glossary_sends_no_prompt():
    recorder = Recorder(httpx2.Response(200, json={"text": "Tak."}))
    transcriber = OpenAITranscriber(KEY, "gpt-transcribe", http_client=recorder.client())
    result = transcriber.transcribe(b"RIFF", "audio/wav", [], operation_id="op-1", language="pl")
    body = recorder.requests[0].content.decode("utf-8", errors="replace")
    assert 'name="prompt"' not in body
    assert 'filename="answer.wav"' in body
    assert result.usage.audio_seconds == 0.0


def test_glossary_truncated_whole_terms_800():
    terms = [f"termin{i:03d}" for i in range(200)]
    prompt = glossary_prompt(terms, "Słownik terminów: ")
    assert prompt.startswith("Słownik terminów: ")
    assert len(prompt) <= 800
    listed = prompt.removeprefix("Słownik terminów: ").split(", ")
    assert listed == terms[: len(listed)]
    assert len(prompt) + len(", ") + len(terms[len(listed)]) > 800
    assert glossary_prompt([], "Słownik terminów: ") is None


def test_unsupported_mime_no_http_call():
    recorder = Recorder(httpx2.Response(200, json={"text": "x"}))
    transcriber = OpenAITranscriber(KEY, "gpt-transcribe", http_client=recorder.client())
    with pytest.raises(ProviderError) as caught:
        transcriber.transcribe(b"x", "audio/flac", [], operation_id="op-1", language="pl")
    assert caught.value.kind == "bad_request"
    assert recorder.requests == []


def test_generate_structured_parses_schema():
    recorder = Recorder(
        httpx2.Response(
            200,
            json=response_body([text_item('{"answer": "tak"}')]),
            headers={"x-request-id": "req_g1"},
        )
    )
    result = generate(recorder)
    assert result.value == Answer(answer="tak")
    assert result.usage.input_tokens == 11
    assert result.usage.output_tokens == 7
    assert result.provenance.model == "gpt-6.1-sol-2026-09-01"
    assert result.provenance.request_id == "req_g1"
    body = json.loads(recorder.requests[0].content)
    assert body["instructions"] == "Jesteś egzaminatorem."
    assert body["input"] == [{"role": "user", "content": "Pytanie?"}]
    assert body["max_output_tokens"] == 16000
    assert body["text"]["format"]["type"] == "json_schema"


def test_generate_structured_refusal():
    recorder = Recorder(
        httpx2.Response(200, json=response_body([{"type": "refusal", "refusal": "Nie pomogę."}]))
    )
    with pytest.raises(ProviderError) as caught:
        generate(recorder)
    assert caught.value.kind == "refused"
    assert "Nie pomogę." in str(caught.value)


def test_generate_structured_invalid_json_invalid_output():
    recorder = Recorder(httpx2.Response(200, json=response_body([text_item('{"answer": ')])))
    with pytest.raises(ProviderError) as caught:
        generate(recorder)
    assert caught.value.kind == "invalid_output"
    assert caught.value.retryable is False


def test_generate_structured_wrong_shape_invalid_output():
    recorder = Recorder(httpx2.Response(200, json=response_body([text_item('{"other": 1}')])))
    with pytest.raises(ProviderError) as caught:
        generate(recorder)
    assert caught.value.kind == "invalid_output"


def test_generate_structured_incomplete_invalid_output():
    recorder = Recorder(
        httpx2.Response(200, json=response_body([text_item('{"answer": "ta')], "incomplete"))
    )
    with pytest.raises(ProviderError) as caught:
        generate(recorder)
    assert caught.value.kind == "invalid_output"
    assert "cut off" in str(caught.value)


def test_status_401_auth_not_retryable():
    recorder = Recorder(error_response(401, "Incorrect API key provided"))
    with pytest.raises(ProviderError) as caught:
        generate(recorder)
    assert caught.value.kind == "auth"
    assert caught.value.retryable is False
    assert caught.value.status_code == 401
    assert len(recorder.requests) == 1


def test_status_429_rate_limit_retryable():
    recorder = Recorder(error_response(429, "Rate limit", FAST_RETRY))
    with pytest.raises(ProviderError) as caught:
        generate(recorder)
    assert caught.value.kind == "rate_limit"
    assert caught.value.retryable is True
    assert len(recorder.requests) == 3


def test_status_500_server_retryable():
    recorder = Recorder(error_response(500, "boom", FAST_RETRY))
    with pytest.raises(ProviderError) as caught:
        generate(recorder)
    assert caught.value.kind == "server"
    assert caught.value.retryable is True
    assert caught.value.status_code == 500


def test_status_400_bad_request():
    recorder = Recorder(error_response(400, "bad schema"))
    with pytest.raises(ProviderError) as caught:
        generate(recorder)
    assert caught.value.kind == "bad_request"
    assert caught.value.retryable is False


def test_network_error_retryable():
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    with pytest.raises(ProviderError) as caught:
        OpenAITextGenerator(KEY, "m", http_client=client).generate_structured(
            "t", "s", [Message(role="user", content="x")], Answer, operation_id="op"
        )
    assert caught.value.kind in {"network", "timeout"}
    assert caught.value.retryable is True


def test_idempotency_header_sent():
    recorder = Recorder(httpx2.Response(200, json=response_body([text_item('{"answer": "tak"}')])))
    generate(recorder)
    assert recorder.requests[0].headers["idempotency-key"] == "op-123"


def test_client_limits():
    transcriber = OpenAITranscriber(KEY, "gpt-transcribe")
    generator_ = OpenAITextGenerator(KEY, "gpt-6.1-sol")
    assert transcriber.client.max_retries == 2
    assert transcriber.client.timeout == 300.0
    assert generator_.client.max_retries == 2
    assert generator_.client.timeout == 120.0
