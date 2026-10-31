"""The openai voice in the service: a Realtime reading, checked word for word, then cached."""

import json
from datetime import datetime
from typing import get_args

import pytest
from conftest import local_client
from pydantic import SecretStr
from test_costs_support import ZONE, FakeEngine, seed_usage, usage_rows
from test_rounds_api import create, start
from test_rounds_support import make_world
from test_topics_support import upgrade
from test_voice_realtime_support import KEY, FakeRealtimeServer

from app.config import Settings
from app.db.session import SessionLocal
from app.features.costs import estimates, spending
from app.features.costs.prices import cost_of
from app.features.costs.spending import SPENDING_LIMIT, guard_for
from app.features.providers.errors import MESSAGES
from app.features.providers.fakes import FakeTranscriber
from app.features.providers.types import ProviderError, Usage
from app.features.providers.usage import get_durable_usage_recorder
from app.features.settings import schemas as settings_schemas
from app.features.voice import service, trial
from app.features.voice.catalog import OPENAI_VOICES
from app.features.voice.openai_realtime import (
    TOO_LONG_FOR_SPEED,
    UNKNOWN_OPENAI_VOICE,
    OpenAIRealtimeReader,
    pcm_to_wav,
)
from app.features.voice.service import VOICE_NOT_VERBATIM, VoiceVerifyTranscriber
from app.main import create_app

MODEL = "gpt-realtime-2.1-mini"
QUESTION = "Omów przyczyny i skutki rewolucji przemysłowej w Europie w XVIII i XIX wieku."
WRONG = QUESTION + " To jest bardzo ciekawe pytanie."


class ScriptedTranscriber(FakeTranscriber):
    """Each call hears the next scripted text (the last one repeats)."""

    def __init__(self, texts: list[str], seconds: float = 0.5) -> None:
        super().__init__()
        self.texts = texts
        self.seconds = seconds

    def transcribe(self, audio, mime, glossary, *, operation_id, language="pl"):
        self.text = self.texts[min(len(self.calls), len(self.texts) - 1)]
        result = super().transcribe(
            audio, mime, glossary, operation_id=operation_id, language=language
        )
        provenance = result.provenance.model_copy(update={"model": "gpt-transcribe"})
        return result.model_copy(
            update={"usage": Usage(audio_seconds=self.seconds), "provenance": provenance}
        )


@pytest.fixture
def keyed(settings) -> Settings:
    upgrade(settings.effective_database_url)
    yield settings.model_copy(update={"openai_api_key": SecretStr(KEY), "voice_provider": "openai"})
    spending._reserved.clear()


@pytest.fixture
def server(monkeypatch):
    with FakeRealtimeServer("ok") as fake:
        monkeypatch.setattr(
            service,
            "get_synthesizer",
            lambda settings, provider: OpenAIRealtimeReader(
                SecretStr(KEY),
                settings.openai_voice_model,
                client=fake.client(),
                sleep=lambda seconds: None,
            ),
        )
        yield fake


class FailingTranscriber(ScriptedTranscriber):
    def __init__(self, error: Exception) -> None:
        super().__init__([QUESTION])
        self.error = error

    def transcribe(self, audio, mime, glossary, *, operation_id, language="pl"):
        raise self.error


def checking(monkeypatch, texts: list[str], fake: ScriptedTranscriber | None = None):
    fake = fake or ScriptedTranscriber(texts)
    monkeypatch.setattr(
        service,
        "verify_transcriber",
        lambda settings: VoiceVerifyTranscriber(
            lambda: fake,
            "openai",
            settings.transcription_model,
            get_durable_usage_recorder(settings),
            guard=guard_for(settings),
        ),
    )
    return fake


def sidecar(settings: Settings, key: str) -> dict:
    path = settings.data_dir / "voice-cache" / f"{key}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def wavs(settings: Settings) -> list:
    return list((settings.data_dir / "voice-cache").glob("*.wav"))


def test_provider_literals_agree():
    expected = ("piper", "azure_speech", "browser", "openai")
    assert get_args(service.VoiceProvider) == get_args(settings_schemas.VoiceProvider) == expected


def test_openai_voices_listed(settings, keyed):
    def listed(configured):
        voices = local_client(create_app(configured)).get("/api/voice/voices").json()
        return voices, [v for v in voices if v["provider"] == "openai"]

    voices, openai = listed(settings)
    assert [v["id"] for v in openai] == list(OPENAI_VOICES)
    assert [(v["label"], v["gender"]) for v in openai] == list(OPENAI_VOICES.values())
    assert all(v["installed"] is False for v in openai)
    providers = [v["provider"] for v in voices]
    assert providers.index("openai") > max(
        i for i, p in enumerate(providers) if p == "azure_speech"
    )
    assert providers[-1] == "browser"
    with_key, openai = listed(keyed)
    assert all(v["installed"] is True for v in openai)
    others = [v for v in with_key if v["provider"] in ("piper", "azure_speech")]
    assert others == [v for v in voices if v["provider"] in ("piper", "azure_speech")]


def test_openai_without_key_503(settings):
    upgrade(settings.effective_database_url)
    client = local_client(create_app(settings))
    response = client.post(
        "/api/voice/speak", json={"text": QUESTION, "provider": "openai", "language": "pl"}
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "No API key for: openai. Add it in Settings."
    assert usage_rows(settings.effective_database_url) == []


def test_checked_reading_is_cached_and_recorded(keyed, server, monkeypatch):
    heard = checking(monkeypatch, [QUESTION])
    result = service.speak(keyed, QUESTION, language="pl", provider="openai")
    assert (result.provider, result.voice, result.cached) == ("openai", "marin", False)
    k16 = result.cache_key[:16]
    reading, check = usage_rows(keyed.effective_database_url)
    assert (reading.operation, reading.role, reading.provider, reading.model) == (
        "voice.synthesize",
        "voice",
        "openai",
        MODEL,
    )
    assert (reading.input_tokens, reading.output_tokens) == (120, 400)
    assert reading.operation_id == f"voice:{k16}"
    usage = Usage(input_tokens=120, output_tokens=400, audio_seconds=0.5, characters=len(QUESTION))
    assert reading.cost_usd == pytest.approx(cost_of("openai", MODEL, "voice", usage))
    assert reading.estimated_usd == pytest.approx(
        estimates.voice_usd("openai", MODEL, len(QUESTION))
    )
    assert (check.operation, check.role, check.provider, check.model) == (
        "voice.verify",
        "transcription",
        "openai",
        keyed.transcription_model,
    )
    assert check.operation_id == f"voice:{k16}:verify"
    [call] = heard.calls
    assert (call["mime"], call["glossary"], call["language"]) == ("audio/wav", [], "pl")
    meta = sidecar(keyed, result.cache_key)
    assert meta["verified"]["attempts"] == 1
    assert meta["verified"]["heard"] == QUESTION
    assert meta["verified"]["model"] == keyed.transcription_model
    assert meta["verified"]["matched"] == 1.0

    again = service.speak(keyed, QUESTION, language="pl", provider="openai")
    assert again.cached is True
    assert server.connections == 1
    assert len(usage_rows(keyed.effective_database_url)) == 2


def test_one_mismatch_then_ok(keyed, server, monkeypatch):
    server.distinct = True
    heard = checking(monkeypatch, [WRONG, QUESTION])
    result = service.speak(keyed, QUESTION, language="pl", provider="openai")
    k = f"voice:{result.cache_key[:16]}"
    rows = usage_rows(keyed.effective_database_url)
    assert [r.operation_id for r in rows] == [k, f"{k}:verify", f"{k}:r1", f"{k}:r1:verify"]
    assert server.connections == 2
    assert sidecar(keyed, result.cache_key)["verified"]["attempts"] == 2
    first, second = (
        pcm_to_wav(b"".join(server.pcm(c, i) for i in range(server.chunks))) for c in (0, 1)
    )
    assert first != second
    assert [call["audio"] for call in heard.calls] == [first, second]
    cached = keyed.data_dir / "voice-cache" / f"{result.cache_key}.wav"
    assert cached.read_bytes() == second


def test_two_mismatches_fall_back(keyed, server, monkeypatch):
    checking(monkeypatch, [WRONG])
    client = local_client(create_app(keyed))
    response = client.post(
        "/api/voice/speak", json={"text": QUESTION, "language": "pl", "provider": "openai"}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == VOICE_NOT_VERBATIM
    assert wavs(keyed) == []
    assert len(usage_rows(keyed.effective_database_url)) == 4
    assert server.connections == 2
    client.post("/api/voice/speak", json={"text": QUESTION, "language": "pl", "provider": "openai"})
    assert server.connections == 4


@pytest.mark.parametrize(
    ("error", "status", "kind"),
    [
        (ProviderError("server", "openai", MESSAGES["server"]), 502, "server"),
        (RuntimeError("check broke"), 500, "RuntimeError"),
    ],
)
def test_check_fails_after_paid_reading(keyed, server, monkeypatch, error, status, kind):
    checking(monkeypatch, [], FailingTranscriber(error))
    client = local_client(create_app(keyed), raise_server_exceptions=False)
    response = client.post(
        "/api/voice/speak", json={"text": QUESTION, "language": "pl", "provider": "openai"}
    )
    assert response.status_code == status
    if status == 502:
        assert response.json()["detail"] == MESSAGES["server"]
    else:
        assert "check broke" not in response.text
    assert wavs(keyed) == []
    assert server.connections == 1  # a failed check is not a mismatch: no second reading
    reading, check = usage_rows(keyed.effective_database_url)
    assert (reading.operation, reading.status) == ("voice.synthesize", "ok")
    assert (reading.input_tokens, reading.output_tokens) == (120, 400)
    assert (check.operation, check.status, check.error_kind) == ("voice.verify", "error", kind)


def test_too_long_for_a_slow_speed_makes_no_call(keyed, server, monkeypatch):
    long_text = (QUESTION + " ") * 18  # about 1,400 characters
    checking(monkeypatch, [long_text])
    client = local_client(create_app(keyed))
    response = client.post(
        "/api/voice/speak",
        json={"text": long_text, "rate": 0.5, "language": "pl", "provider": "openai"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == TOO_LONG_FOR_SPEED
    assert server.connections == 0
    assert usage_rows(keyed.effective_database_url) == []
    assert (
        client.post(
            "/api/voice/speak", json={"text": long_text, "language": "pl", "provider": "openai"}
        ).status_code
        == 200
    )
    assert server.connections == 1


def test_check_blocked_by_ceiling(keyed, server, monkeypatch):
    keyed = keyed.model_copy(update={"spending_ceiling_usd": 1.0})
    checking(monkeypatch, [QUESTION])
    url = keyed.effective_database_url
    server.on_response_create = lambda: seed_usage(url, 1.0, started_at=datetime.now(ZONE))
    client = local_client(create_app(keyed))
    response = client.post(
        "/api/voice/speak", json={"text": QUESTION, "language": "pl", "provider": "openai"}
    )
    assert response.status_code == 409
    # $1.00 seeded plus the reading (120 / 400 tokens, about $0.008).
    assert response.json()["detail"] == SPENDING_LIMIT.format(spent="$1.01", ceiling="$1.00")
    assert wavs(keyed) == []
    rows = [r for r in usage_rows(url) if r.operation != "attempt.assess"]
    assert [(r.operation, r.status) for r in rows] == [
        ("voice.synthesize", "ok"),
        ("voice.verify", "blocked"),
    ]


def test_reading_blocked_by_ceiling(keyed, server, monkeypatch):
    keyed = keyed.model_copy(update={"spending_ceiling_usd": 1.0})
    checking(monkeypatch, [QUESTION])
    url = keyed.effective_database_url
    seed_usage(url, 1.0, started_at=datetime.now(ZONE))
    response = local_client(create_app(keyed)).post(
        "/api/voice/speak", json={"text": QUESTION, "language": "pl", "provider": "openai"}
    )
    assert response.status_code == 409
    assert server.connections == 0
    [row] = [r for r in usage_rows(url) if r.operation != "attempt.assess"]
    assert (row.status, row.provider, row.operation) == ("blocked", "openai", "voice.synthesize")


def test_openai_resolve_voice(keyed):
    def configured(name):
        return keyed.model_copy(update={"voice_name": name})

    assert service.resolve_voice(configured("pl_PL-bass-high"), "openai", None) == "marin"
    assert service.resolve_voice(configured("cedar"), "openai", None) == "cedar"
    assert service.resolve_voice(configured("marin"), "piper", None) == "pl_PL-bass-high"
    assert (
        service.resolve_voice(configured("marin"), "azure_speech", None)
        == keyed.azure_voice_default
    )
    for provider in ("openai", "piper", "azure_speech"):
        assert service.resolve_voice(configured("marin"), provider, "x-voice") == "x-voice"


def test_unknown_openai_voice_makes_no_call(keyed, server, monkeypatch):
    checking(monkeypatch, [QUESTION])
    client = local_client(create_app(keyed))
    response = client.post(
        "/api/voice/speak",
        json={"text": QUESTION, "voice": "nobody", "language": "pl", "provider": "openai"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == UNKNOWN_OPENAI_VOICE
    assert server.connections == 0
    assert usage_rows(keyed.effective_database_url) == []


def test_cache_key_parts(keyed, server, monkeypatch):
    checking(monkeypatch, [QUESTION])
    first = service.speak(keyed, QUESTION, language="pl", provider="openai")
    faster = service.speak(keyed, QUESTION, language="pl", provider="openai", rate=1.2)
    other = service.speak(keyed, QUESTION, language="pl", provider="openai", voice="cedar")
    newer = service.speak(
        keyed.model_copy(update={"openai_voice_model": "gpt-realtime-x"}),
        QUESTION,
        language="pl",
        provider="openai",
    )
    keys = {r.cache_key for r in (first, faster, other, newer)}
    assert len(keys) == 4
    assert server.connections == 4
    meta = sidecar(keyed, first.cache_key)
    assert meta["engine_version"] == f"openai-realtime:{MODEL}:reader-v1"


def test_settings_accept_openai(settings):
    client = local_client(create_app(settings))
    response = client.put("/api/settings", json={"voice_provider": "openai", "voice_name": "marin"})
    assert response.status_code == 200, response.text
    written = settings.user_env_file.read_text(encoding="utf-8")
    assert "AOC_VOICE_PROVIDER=openai" in written
    assert "AOC_VOICE_NAME=marin" in written
    body = client.get("/api/settings").json()
    assert (body["voice_provider"], body["voice_name"]) == ("openai", "marin")


def test_round_snapshot_with_openai(client, settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as db:
        world = make_world(db)
    audio = {"mode": "premium", "provider": "openai", "voice": "cedar", "rate": 1.1}
    state = start(client, world, question_count=1, question_audio=audio)
    assert state["config"]["question_audio"] == audio
    client.app.state.settings = settings.model_copy(
        update={"voice_provider": "openai", "voice_name": "marin"}
    )
    response = create(client, world, question_count=1, question_audio={"mode": "premium"})
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["config"]["question_audio"] == {
        "mode": "premium",
        "provider": "openai",
        "voice": "marin",
        "rate": settings.voice_rate,
    }
    again = client.get(f"/api/rounds/{created['id']}")
    assert again.status_code == 200
    assert again.json()["config"]["question_audio"]["provider"] == "openai"


def test_trial_has_one_unchecked_openai_column(keyed, monkeypatch, tmp_path):
    assert [v for v in trial.available_voices(keyed) if v[0] == "openai"] == [("openai", "marin")]
    assert trial._label("openai", "marin") == "Marin (OpenAI Realtime, recommended)"
    engine = FakeEngine()
    monkeypatch.setattr(service, "get_synthesizer", lambda settings, provider: engine)
    checks = []
    monkeypatch.setattr(service, "verify_transcriber", lambda settings: checks.append(1))
    trial.render_trial(keyed, tmp_path / "trial", [("openai", "marin")])
    rows = usage_rows(keyed.effective_database_url)
    assert len(rows) == len(trial.SAMPLE_QUESTIONS) == len(engine.calls)
    assert {(r.operation, r.provider) for r in rows} == {("voice.synthesize", "openai")}
    assert checks == []
