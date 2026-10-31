"""The OpenAI Realtime voice: config, price, estimates and how its rows are counted."""

import math
from pathlib import Path

import pytest
from test_costs_support import NOW, ZONE, seed_usage
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.config import Settings
from app.db.session import SessionLocal
from app.features.costs import estimates, prices, router, spending
from app.features.costs.prices import PRICES, Price, cost_of, price_for
from app.features.providers.types import Usage

BACKEND = Path(__file__).resolve().parents[1]
REALTIME = ("openai", "gpt-realtime-2.1-mini")
MODEL = "gpt-realtime-2.1-mini"


@pytest.fixture
def url(settings):
    upgrade(settings.effective_database_url)
    return settings.effective_database_url


@pytest.fixture
def db(url):
    with SessionLocal(url) as session:
        yield session


def test_config_fields(monkeypatch):
    for key in ("AOC_OPENAI_VOICE_MODEL", "AOC_OPENAI_VOICE_DEFAULT", "AOC_VOICE_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.openai_voice_model == MODEL
    assert settings.openai_voice_default == "marin"
    monkeypatch.setenv("AOC_OPENAI_VOICE_MODEL", "gpt-realtime-x")
    monkeypatch.setenv("AOC_OPENAI_VOICE_DEFAULT", "cedar")
    monkeypatch.setenv("AOC_VOICE_PROVIDER", "openai")
    changed = Settings(_env_file=None)
    assert (changed.openai_voice_model, changed.openai_voice_default) == ("gpt-realtime-x", "cedar")
    assert changed.voice_provider == "openai"
    example = (BACKEND / "env-example.txt").read_text(encoding="utf-8")
    assert f"AOC_OPENAI_VOICE_MODEL={MODEL}" in example
    assert "AOC_OPENAI_VOICE_DEFAULT=marin" in example


def test_realtime_voice_price():
    expected = Price(input_per_m=0.60, output_per_m=20.0, kind="voice")
    assert price_for(*REALTIME, "voice") == (expected, True)
    usage = Usage(input_tokens=100, output_tokens=400)
    assert cost_of(*REALTIME, "voice", usage) == pytest.approx(100 * 0.6 / 1e6 + 400 * 20 / 1e6)
    unknown, known = price_for("openai", "gpt-realtime-x", "voice")
    assert (unknown.input_per_m, unknown.output_per_m, known) == (0.60, 20.0, False)
    assert unknown.kind == "voice"


def test_existing_price_kinds_unchanged():
    assert prices._highest("openai", "generation") == Price(input_per_m=2, output_per_m=10)
    assert prices._highest("openai", "transcription") == Price(audio_per_min=0.0045)
    before = {
        ("openai", "gpt-transcribe"): "transcription",
        ("openai", "gpt-6.1-sol"): "text",
        ("anthropic", "claude-sonnet-5-5"): "text",
        ("azure_speech", "neural"): "voice",
        ("piper", "local"): "text",
    }
    for key, kind in before.items():
        assert prices._model_kind(PRICES[key]) == kind, key
        assert router._role(PRICES[key]) == kind, key
    assert prices._model_kind(PRICES[REALTIME]) == "voice"
    assert router._role(PRICES[REALTIME]) == "voice"


def test_prices_endpoint_lists_realtime_voice(client):
    rows = client.get("/api/costs/prices").json()
    row = next(r for r in rows if r["model"] == MODEL)
    assert row["provider"] == "openai"
    assert row["role"] == "voice"
    assert (row["input_per_m"], row["output_per_m"]) == (0.6, 20)
    assert row["checked"] == "2026-10-20"


def test_voice_estimates(settings):
    expected = estimates.tokens_for(240 + 250) * 0.6 / 1e6 + math.ceil(240 / 12 * 20) * 20 / 1e6
    assert estimates.voice_usd("openai", MODEL, 240) == pytest.approx(expected)
    assert estimates.voice_usd("azure_speech", "", 240) == pytest.approx(240 * 16 / 1e6)
    assert estimates.voice_usd("piper", "local", 240) == 0
    assert estimates.verify_usd(settings, 240) == pytest.approx((240 / 12) / 60 * 0.0045)


def test_round_estimate_openai_voice_part(client, db):
    world = make_world(db)
    settings = client.app.state.settings.model_copy(update={"voice_provider": "openai"})
    sid = world["subject"].id

    def estimate(**extra):
        return estimates.round_estimate(
            db,
            settings,
            subject_id=sid,
            question_count=10,
            answer_seconds=120,
            follow_ups=True,
            **extra,
        )

    found = estimate()
    chars = round(20 * estimates.VOICE_CHARS_PER_QUESTION)
    voice = next(p for p in found.parts if p.operation == "voice.synthesize")
    assert voice.usd == pytest.approx(
        estimates.voice_usd("openai", MODEL, chars) + estimates.verify_usd(settings, chars)
    )
    assert found.voice_provider == "openai"
    assert voice.usd / 20 == pytest.approx(0.008, abs=0.0005)  # about $0.008 a question
    piper = estimate(voice_provider="piper")
    assert "voice.synthesize" not in [p.operation for p in piper.parts]
    azure = estimate(voice_provider="azure_speech")
    assert azure.parts[-1].usd == pytest.approx(chars * 16 / 1e6)


def test_spend_and_summary_count_verify(client, url, db):
    seed_usage(
        url,
        None,
        operation="voice.synthesize",
        role="voice",
        provider="openai",
        model=MODEL,
        input_tokens=100,
        output_tokens=400,
    )
    seed_usage(
        url,
        None,
        operation="voice.verify",
        role="transcription",
        provider="openai",
        model="gpt-transcribe",
        audio_seconds=20.0,
    )
    reading = cost_of(*REALTIME, "voice", Usage(input_tokens=100, output_tokens=400))
    check = cost_of("openai", "gpt-transcribe", "transcription", Usage(audio_seconds=20.0))
    _, start, end = spending.month_of("2026-10", ZONE)
    assert spending.spent_between(db, start, end) == pytest.approx(reading + check)
    summary = client.get("/api/costs/summary?month=2026-10").json()
    assert summary["voice_usd"] == pytest.approx(reading + check)
    rows = client.get("/api/costs/usage?month=2026-10&group=subject").json()["rows"]
    keyed = {r["key"]: r for r in rows}
    assert keyed["voice"]["reported_usd"] == pytest.approx(reading + check)
    assert keyed["voice"]["requests"] == 2
    assert NOW.month == 10
