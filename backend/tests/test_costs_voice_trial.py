"""The voice trial's Azure readings are paid calls: recorded and guarded like the others."""

from datetime import datetime

import pytest
from pydantic import SecretStr
from test_costs_support import ZONE, FakeEngine, seed_usage, usage_rows
from test_topics_support import upgrade

from app.features.costs import spending
from app.features.voice import service
from app.features.voice.trial import SAMPLE_QUESTIONS, render_trial


@pytest.fixture
def azure(settings, monkeypatch):
    upgrade(settings.effective_database_url)
    engine = FakeEngine()
    monkeypatch.setattr(service, "get_synthesizer", lambda settings, provider: engine)
    configured = settings.model_copy(
        update={"azure_speech_key": SecretStr("k" * 32), "azure_speech_region": "westeurope"}
    )
    yield configured, engine
    spending._reserved.clear()


def test_trial_azure_readings_are_recorded(azure, tmp_path):
    settings, engine = azure
    render_trial(settings, tmp_path / "trial", [("azure_speech", "pl-PL-ZofiaNeural")])
    rows = usage_rows(settings.effective_database_url)
    assert len(rows) == len(SAMPLE_QUESTIONS) == len(engine.calls)
    assert {(r.operation, r.provider, r.status) for r in rows} == {
        ("voice.synthesize", "azure_speech", "ok")
    }


def test_trial_piper_is_not_recorded(azure, tmp_path):
    settings, _ = azure
    render_trial(settings, tmp_path / "trial", [("piper", "pl_PL-gosia-medium")])
    assert usage_rows(settings.effective_database_url) == []


def test_trial_respects_the_ceiling(azure, tmp_path):
    settings, engine = azure
    settings = settings.model_copy(update={"spending_ceiling_usd": 1.0})
    seed_usage(settings.effective_database_url, 1.0, started_at=datetime.now(ZONE))
    page = render_trial(settings, tmp_path / "trial", [("azure_speech", "pl-PL-ZofiaNeural")])
    assert engine.calls == []
    assert "The monthly spending ceiling is reached" in page.read_text("utf-8")
