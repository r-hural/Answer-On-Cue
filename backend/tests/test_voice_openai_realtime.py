import io
import logging
import math
import threading
import time
import traceback
import wave
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from test_voice_realtime_support import KEY, RESPONSE_ID, FakeRealtimeServer

from app.features.costs.estimates import AUDIO_OUTPUT_TOKENS_PER_SECOND, VOICE_CHARS_PER_SECOND
from app.features.providers import limits
from app.features.providers.types import ProviderError, Usage, VoiceSpec
from app.features.voice import openai_realtime
from app.features.voice.cache import wav_duration_ms
from app.features.voice.openai_realtime import (
    MAX_OUTPUT_TOKENS,
    READ_ALOUD,
    RETRY_DELAY,
    TOO_LONG_FOR_SPEED,
    OpenAIRealtimeReader,
    _event_kind,
    output_tokens_needed,
    reading_instructions,
    session_timeout,
)
from app.features.voice.service import MAX_TEXT_LENGTH

MODEL = "gpt-realtime-2.1-mini"
QUESTION = "Wyjaśnij różnicę między PKB a PNB i podaj przykład z gospodarki."


class Sleeps(list):
    def __call__(self, seconds: float) -> None:
        self.append(seconds)


def reader(server: FakeRealtimeServer, sleeps: Sleeps | None = None) -> OpenAIRealtimeReader:
    return OpenAIRealtimeReader(
        SecretStr(KEY),
        MODEL,
        client=server.client(),
        sleep=sleeps if sleeps is not None else Sleeps(),
    )


def read(server, text=QUESTION, voice="marin", rate=1.0, sleeps=None, **kwargs):
    return reader(server, sleeps).synthesize(
        text, VoiceSpec(voice=voice, rate=rate), operation_id="voice:abc", **kwargs
    )


def failure(server, sleeps=None, **kwargs) -> ProviderError:
    with pytest.raises(ProviderError) as info:
        read(server, sleeps=sleeps, **kwargs)
    return info.value


def test_session_is_one_exact_reading():
    with FakeRealtimeServer("ok") as server:
        read(server, rate=1.2)
        assert server.closed.wait(3)
    assert server.paths == [f"/v1/realtime?model={MODEL}"]
    assert server.authorizations == [f"Bearer {KEY}"]
    events = server.events[0]
    assert [e["type"] for e in events] == [
        "session.update",
        "conversation.item.create",
        "response.create",
    ]
    assert events[0]["session"] == {
        "type": "realtime",
        "output_modalities": ["audio"],
        "instructions": reading_instructions(None),
        "audio": {
            "output": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "voice": "marin",
                "speed": 1.2,
            },
            "input": {"turn_detection": None},
        },
        "max_output_tokens": 4096,
    }
    assert events[1]["item"] == {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": QUESTION}],
    }
    assert "response" not in events[2]


def test_instructions():
    assert reading_instructions(None) == READ_ALOUD.format(language="")
    assert "word for word." in reading_instructions(None)
    assert "word for word, in Polish." in reading_instructions("Polish")
    with FakeRealtimeServer("ok") as server:
        read(server, language_name="Polish")
    assert server.events[0][0]["session"]["instructions"] == reading_instructions("Polish")


def test_audio_becomes_wav():
    with FakeRealtimeServer("ok") as server:
        result = read(server)
    with wave.open(io.BytesIO(result.audio)) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 24000)
        assert wav.getnframes() == 12_000
    assert wav_duration_ms(result.audio) == 500
    assert result.mime == "audio/wav"
    with FakeRealtimeServer("ok") as server:
        server.odd_byte = True
        odd = read(server)
    assert len(odd.audio) == len(result.audio)  # the odd trailing byte is dropped


def test_usage_and_provenance():
    with FakeRealtimeServer("ok") as server:
        result = read(server)
    assert result.usage == Usage(
        input_tokens=120, output_tokens=400, audio_seconds=0.5, characters=len(QUESTION)
    )
    assert result.provenance.provider == "openai"
    assert result.provenance.model == MODEL
    assert result.provenance.request_id == RESPONSE_ID
    assert result.provenance.operation_id == "voice:abc"


def test_engine_version():
    with FakeRealtimeServer("ok") as server:
        engine = reader(server)
        assert engine.engine_version("marin") == f"openai-realtime:{MODEL}:reader-v1"
        assert engine.engine_version("cedar") == engine.engine_version("marin")
        assert server.connections == 0


def test_handshake_errors_and_retries():
    with FakeRealtimeServer("reject:401") as server:
        assert failure(server).kind == "auth"
        assert server.connections == 1
    sleeps = Sleeps()
    with FakeRealtimeServer("reject:429") as server:
        assert failure(server, sleeps=sleeps).kind == "rate_limit"
        assert server.connections == 1 + limits.SDK_MAX_RETRIES
    assert sleeps == [RETRY_DELAY] * limits.SDK_MAX_RETRIES
    sleeps = Sleeps()
    with FakeRealtimeServer(["reject:503", "ok"]) as server:
        assert read(server, sleeps=sleeps).usage.output_tokens == 400
        assert server.connections == 2
    assert sleeps == [RETRY_DELAY]


def test_error_event():
    with FakeRealtimeServer("error") as server:
        error = failure(server)
        assert server.connections == 1
    assert error.kind == "bad_request"
    assert "secret detail" not in str(error)
    assert "secret detail" not in error.message


@pytest.mark.parametrize(
    ("behaviour", "kind"),
    [("error_rate", "rate_limit"), ("error_server", "server")],
)
def test_error_event_kinds_are_not_retried(behaviour, kind):
    sleeps = Sleeps()
    with FakeRealtimeServer(behaviour) as server:
        error = failure(server, sleeps=sleeps)
        assert server.connections == 1
    assert error.kind == kind
    assert sleeps == []  # after response.create: billed, so never retried


def test_event_kind_mapping():
    def kind(type_=None, code=None):
        return _event_kind(SimpleNamespace(type=type_, code=code))

    assert kind("invalid_request_error", "rate_limit_exceeded") == "rate_limit"
    assert kind("invalid_request_error", "x") == "bad_request"
    assert kind("server_error", "x") == "server"
    assert kind() == "server"
    assert _event_kind(None) == "server"


@pytest.mark.parametrize(
    ("behaviour", "kind"),
    [
        ("incomplete", "invalid_output"),
        ("failed", "server"),
        ("no_audio", "invalid_output"),
        ("drop", "network"),
        ("bad_base64", "invalid_output"),
        ("not_json", "invalid_output"),
        ("no_delta", "invalid_output"),
    ],
)
def test_bad_responses(behaviour, kind):
    sleeps = Sleeps()
    with FakeRealtimeServer(behaviour) as server:
        assert failure(server, sleeps=sleeps).kind == kind
        assert server.connections == 1
    assert sleeps == []


def test_stall_times_out_without_leaks(monkeypatch):
    monkeypatch.setattr(openai_realtime, "SESSION_BASE_TIMEOUT", 0.3)
    with FakeRealtimeServer("stall") as server:
        baseline = threading.active_count()
        started = time.monotonic()
        error = failure(server, text="Krótko.")
        elapsed = time.monotonic() - started
        assert error.kind == "timeout"
        assert elapsed < 3
        assert server.closed.wait(3)
        assert server.connections == 1
        deadline = time.monotonic() + 3
        while threading.active_count() > baseline and time.monotonic() < deadline:
            time.sleep(0.02)
        assert threading.active_count() <= baseline


def test_output_budget_covers_the_text_limit():
    needed = math.ceil(MAX_TEXT_LENGTH / VOICE_CHARS_PER_SECOND * AUDIO_OUTPUT_TOKENS_PER_SECOND)
    assert needed <= MAX_OUTPUT_TOKENS
    assert session_timeout(200) == 20.0 + 200 / 12


def test_too_long_for_a_slow_speed_makes_no_call():
    long_text = (QUESTION + " ") * 22  # about 1,400 characters
    assert output_tokens_needed(len(long_text), 0.5) > MAX_OUTPUT_TOKENS
    assert output_tokens_needed(len(long_text), 1.0) <= MAX_OUTPUT_TOKENS
    with FakeRealtimeServer("ok") as server:
        error = failure(server, text=long_text, rate=0.5)
        assert server.connections == 0
        assert read(server, text=long_text, rate=1.0).usage.output_tokens == 400
        assert server.connections == 1
    assert (error.kind, error.message) == ("bad_request", TOO_LONG_FOR_SPEED)
    assert not error.retryable


def test_key_not_in_errors_or_logs(caplog):
    caplog.set_level(logging.DEBUG)
    for behaviour in ("reject:401", "error"):
        with FakeRealtimeServer(behaviour) as server:
            error = failure(server)
        rendered = "".join(traceback.format_exception(error))
        assert KEY not in rendered
        assert KEY not in repr(error)
    assert KEY not in caplog.text
