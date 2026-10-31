"""Every provider call stores its pre-call estimate and its cost."""

import pytest
from alembic import command
from pydantic import BaseModel, SecretStr
from sqlalchemy import create_engine, inspect, text
from test_costs_support import (
    FakeEngine,
    PaidTextGenerator,
    PaidTranscriber,
    durable,
    migration_config,
    usage_rows,
)
from test_topics_support import upgrade

from app.config import Settings
from app.features.costs import estimates
from app.features.costs.prices import PRICES, cost_of, price_for
from app.features.providers.fakes import FakeTextGenerator, silent_wav
from app.features.providers.registry import RecordingTextGenerator, RecordingTranscriber
from app.features.providers.types import Message, ProviderError, Usage
from app.features.voice import service as voice

TEXT = "Wyjaśnij różnicę między PKB a PNB i podaj przykład."


class Out(BaseModel):
    x: int


@pytest.fixture
def url(settings):
    upgrade(settings.effective_database_url)
    return settings.effective_database_url


def test_migration_adds_cost_columns(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/m.db"
    cfg = migration_config(url)
    command.upgrade(cfg, "head")
    inspector = inspect(create_engine(url))
    columns = {c["name"]: c for c in inspector.get_columns("usage_event")}
    assert columns["estimated_usd"]["nullable"] and columns["cost_usd"]["nullable"]
    assert inspector.get_foreign_keys("usage_event") == []
    with create_engine(url).begin() as conn:
        conn.execute(
            text(
                "INSERT INTO usage_event (operation_id, operation, task, role, provider, model,"
                " status, started_at, duration_ms, estimated_usd, cost_usd) VALUES"
                " ('op', 'attempt.assess', 't', 'assessment', 'openai', 'm', 'ok',"
                " '2026-10-01 00:00:00', 1, 0.1, 0.2)"
            )
        )
    command.downgrade(cfg, "0013_chapters")
    inspector = inspect(create_engine(url))
    names = {c["name"] for c in inspector.get_columns("usage_event")}
    assert "estimated_usd" not in names and "cost_usd" not in names
    with create_engine(url).connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM usage_event")).scalar_one() == 1
    command.upgrade(cfg, "head")
    names = {c["name"] for c in inspect(create_engine(url)).get_columns("usage_event")}
    assert {"estimated_usd", "cost_usd"} <= names


def test_prices():
    assert cost_of("openai", "gpt-transcribe", "transcription", Usage(audio_seconds=120)) == (
        pytest.approx(0.009)
    )
    tokens = Usage(input_tokens=10_000, output_tokens=1_000)
    assert cost_of("openai", "gpt-6.1-sol", "generation", tokens) == pytest.approx(0.03)
    assert cost_of(
        "azure_speech", "pl-PL-ZofiaNeural", "voice", Usage(characters=1000)
    ) == pytest.approx(0.016)
    price, known = price_for("openai", "gpt-unknown", "assessment")
    assert known is False
    highest = max(
        (
            p
            for (name, _), p in PRICES.items()
            if name == "openai" and p.output_per_m and not p.kind
        ),
        key=lambda p: p.output_per_m,
    )
    assert (price.input_per_m, price.output_per_m) == (highest.input_per_m, highest.output_per_m)
    assert price_for("openai", "gpt-6.1-sol", "generation")[1] is True
    assert cost_of("piper", "pl_PL-bass-high", "voice", Usage(characters=5000)) == 0


def test_text_call_row_has_estimate_and_cost(url):
    fake = PaidTextGenerator({"questions.generate": Out(x=1)})
    generator = RecordingTextGenerator(
        lambda: fake, "openai", "gpt-6.1-sol", "generation", durable(url)
    )
    system, prompt = "s" * 300, "p" * 3000
    generator.generate_structured(
        "questions.generate",
        system,
        [Message(role="user", content=prompt)],
        Out,
        operation_id="questions.generate:1:0",
    )
    [row] = usage_rows(url)
    assert row.cost_usd == pytest.approx(0.03)
    expected = estimates.text_call_usd(
        "openai",
        "gpt-6.1-sol",
        "generation",
        len(system) + len(prompt),
        estimates.TYPICAL_OUTPUT_TOKENS["questions.generate"],
    )
    assert row.estimated_usd == pytest.approx(expected) and row.estimated_usd > 0
    # an unlisted task falls back to the typical 2 000 output tokens
    assert estimates.typical_output("other.task") == estimates.TYPICAL_OUTPUT_FALLBACK == 2000


def test_error_row_has_estimate_but_no_cost(url):
    error = ProviderError("server", "openai", "Provider failure: server.")
    generator = RecordingTextGenerator(
        lambda: FakeTextGenerator({}, error=error),
        "openai",
        "gpt-6.1-sol",
        "generation",
        durable(url),
    )
    with pytest.raises(ProviderError):
        generator.generate_structured(
            "questions.generate", "s", [], Out, operation_id="questions.generate:1:0"
        )
    [row] = usage_rows(url)
    assert row.status == "error"
    assert row.estimated_usd is not None and row.estimated_usd > 0
    assert row.cost_usd is None


def test_transcription_estimate_from_audio(url):
    fake = PaidTranscriber(seconds=90)
    transcriber = RecordingTranscriber(lambda: fake, "openai", "gpt-transcribe", durable(url))
    transcriber.transcribe(
        silent_wav(3.0), "audio/wav", [], operation_id="attempt-1:transcribe:1", language="pl"
    )
    webm = b"\x1a" * 8000
    transcriber.transcribe(
        webm, "audio/webm", [], operation_id="attempt-1:transcribe:2", language="pl"
    )
    wav_row, webm_row = usage_rows(url)
    assert wav_row.estimated_usd == pytest.approx(3 / 60 * 0.0045)
    assert webm_row.estimated_usd == pytest.approx(2 / 60 * 0.0045)
    assert wav_row.cost_usd == pytest.approx(90 / 60 * 0.0045)


@pytest.fixture
def azure(settings, monkeypatch, url) -> tuple[Settings, FakeEngine]:
    engine = FakeEngine()
    monkeypatch.setattr(voice, "get_synthesizer", lambda settings, provider: engine)
    configured = settings.model_copy(
        update={
            "voice_provider": "azure_speech",
            "azure_speech_key": SecretStr("k" * 32),
            "azure_speech_region": "westeurope",
        }
    )
    return configured, engine


def test_azure_voice_is_recorded(azure, url):
    settings, engine = azure
    result = voice.speak(settings, TEXT, provider=settings.voice_provider)
    [row] = usage_rows(url)
    assert (row.operation, row.role, row.provider, row.task) == (
        "voice.synthesize",
        "voice",
        "azure_speech",
        "voice.synthesize",
    )
    assert row.operation_id == f"voice:{result.cache_key[:16]}"
    assert row.characters == len(TEXT)
    assert row.cost_usd == pytest.approx(len(TEXT) * 16 / 1_000_000)
    assert row.estimated_usd == pytest.approx(len(TEXT) * 16 / 1_000_000)
    assert row.subject_id is None
    voice.speak(settings, TEXT, provider=settings.voice_provider)  # cached: no call, no row
    assert len(engine.calls) == 1 and len(usage_rows(url)) == 1
    voice.speak(settings, TEXT + " Drugie.", provider="piper")
    assert len(usage_rows(url)) == 1


def test_unreported_usage_is_stored_at_its_estimate(url):
    """A transcription response without a duration still counts: cost = the pre-call estimate."""
    fake = PaidTranscriber(seconds=0.0)  # the provider reported no duration and no tokens
    transcriber = RecordingTranscriber(lambda: fake, "openai", "gpt-transcribe", durable(url))
    transcriber.transcribe(
        silent_wav(3.0), "audio/wav", [], operation_id="attempt-1:transcribe:1", language="pl"
    )
    [row] = usage_rows(url)
    assert row.status == "ok"
    assert row.estimated_usd == pytest.approx(3 / 60 * 0.0045)
    assert row.cost_usd == pytest.approx(row.estimated_usd)
