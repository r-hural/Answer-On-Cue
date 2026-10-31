"""Regression: an Azure reading through the real adapter writes one usage row (not checked)."""

import httpx2
import pytest
from conftest import local_client
from pydantic import SecretStr
from test_costs_support import usage_rows
from test_topics_support import upgrade

from app.features.providers.fakes import silent_wav
from app.features.voice import service
from app.features.voice.azure_adapter import AzureSynthesizer
from app.main import create_app

TEXT = "Wyjaśnij różnicę między PKB a PNB i podaj przykład."


def test_azure_synthesis_writes_a_usage_row(settings, monkeypatch):
    upgrade(settings.effective_database_url)
    settings = settings.model_copy(
        update={"azure_speech_key": SecretStr("k" * 32), "azure_speech_region": "westeurope"}
    )
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(200, content=silent_wav(0.4, 24000), headers={"X-RequestId": "r"})

    engine = AzureSynthesizer(
        settings.azure_speech_key,
        "westeurope",
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    monkeypatch.setattr(service, "get_synthesizer", lambda settings, provider: engine)
    checks = []
    monkeypatch.setattr(service, "verify_transcriber", lambda settings: checks.append(1))
    client = local_client(create_app(settings))
    response = client.post("/api/voice/speak", json={"text": TEXT, "provider": "azure_speech"})
    assert response.status_code == 200, response.text
    [row] = usage_rows(settings.effective_database_url)
    assert (row.provider, row.role, row.operation, row.status) == (
        "azure_speech",
        "voice",
        "voice.synthesize",
        "ok",
    )
    assert row.characters == len(TEXT)
    assert row.cost_usd == pytest.approx(len(TEXT) * 16 / 1e6)
    again = client.post("/api/voice/speak", json={"text": TEXT, "provider": "azure_speech"})
    assert again.json()["cached"] is True
    assert len(usage_rows(settings.effective_database_url)) == 1
    assert len(calls) == 1
    assert checks == []
