import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.features.providers.fakes import FakeSynthesizer
from app.features.providers.types import VoiceSpec
from app.features.voice import service
from app.main import create_app

QUESTION = "Wyjaśnij różnicę między PKB a PNB i podaj przykład."


class FakeEngine(FakeSynthesizer):
    """FakeSynthesizer plus the engine version the voice service asks for."""

    def __init__(self, version: str = "fake-1") -> None:
        super().__init__()
        self.version = version

    def engine_version(self, voice: str) -> str:
        return self.version


@pytest.fixture
def engine(monkeypatch) -> FakeEngine:
    fake = FakeEngine()
    monkeypatch.setattr(service, "get_synthesizer", lambda settings, provider: fake)
    return fake


def test_speak_returns_audio_url_and_caches(client, engine):
    first = client.post("/api/voice/speak", json={"text": QUESTION, "provider": "piper"})
    assert first.status_code == 200
    body = first.json()
    assert body["provider"] == "piper"
    assert body["voice"] == "pl_PL-bass-high"
    assert body["rate"] == 1.0
    assert body["cached"] is False
    assert body["duration_ms"] == 500
    assert body["audio_url"].startswith("/api/voice/cache/")
    assert body["audio_url"].endswith(".wav")
    second = client.post(
        "/api/voice/speak", json={"text": "  " + QUESTION + "\n", "provider": "piper"}
    ).json()
    assert second["cached"] is True
    assert second["audio_url"] == body["audio_url"]
    assert second["duration_ms"] == 500
    assert len(engine.calls) == 1
    assert engine.calls[0]["text"] == QUESTION
    assert engine.calls[0]["voice"] == VoiceSpec(voice="pl_PL-bass-high", rate=1.0)


def test_speak_new_file_after_settings_change(client, engine):
    first = client.post("/api/voice/speak", json={"text": QUESTION, "provider": "piper"}).json()
    faster = client.post(
        "/api/voice/speak", json={"text": QUESTION, "rate": 1.2, "provider": "piper"}
    ).json()
    engine.version = "fake-2"
    upgraded = client.post("/api/voice/speak", json={"text": QUESTION, "provider": "piper"}).json()
    assert len({first["audio_url"], faster["audio_url"], upgraded["audio_url"]}) == 3
    assert len(engine.calls) == 3


def test_cache_endpoint_serves_wav(client, engine):
    url = client.post("/api/voice/speak", json={"text": QUESTION, "provider": "piper"}).json()[
        "audio_url"
    ]
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content[:4] == b"RIFF"


@pytest.mark.parametrize(
    "name", ["abc.wav", "A" * 64 + ".wav", "a" * 63 + "g.wav", "a" * 64 + ".mp3", "..%2Fx.wav"]
)
def test_cache_endpoint_bad_key_404(client, name):
    assert client.get(f"/api/voice/cache/{name}").status_code == 404


def test_cache_endpoint_missing_file_404(client):
    assert client.get(f"/api/voice/cache/{'a' * 64}.wav").status_code == 404


def test_speak_browser_provider_null_url(client, engine):
    body = client.post(
        "/api/voice/speak", json={"text": QUESTION, "provider": "browser", "rate": 0.9}
    ).json()
    assert body == {
        "provider": "browser",
        "voice": "browser",
        "rate": 0.9,
        "audio_url": None,
        "cached": False,
        "duration_ms": None,
    }
    assert engine.calls == []


@pytest.mark.parametrize("payload", [{"text": ""}, {"text": "   \n"}, {"text": "a" * 2001}, {}])
def test_speak_text_validation_422(client, engine, payload):
    response = client.post("/api/voice/speak", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"] == "The question text is empty or too long."
    assert engine.calls == []


def test_speak_text_limit_is_inclusive(client, engine):
    assert (
        client.post("/api/voice/speak", json={"text": "a" * 2000, "provider": "piper"}).status_code
        == 200
    )


def test_speak_rate_out_of_range_422(client, engine):
    response = client.post(
        "/api/voice/speak", json={"text": QUESTION, "rate": 2.0, "provider": "piper"}
    )
    assert response.status_code == 422


def test_speak_piper_not_installed_503(client):
    response = client.post("/api/voice/speak", json={"text": QUESTION, "provider": "piper"})
    assert response.status_code == 503
    assert response.json()["detail"] == (
        "The Piper voice is not installed. Run scripts/get-piper-voice.ps1."
    )


def test_azure_without_key_503(client, settings):
    assert settings.azure_speech_key is None
    response = client.post("/api/voice/speak", json={"text": QUESTION, "provider": "azure_speech"})
    assert response.status_code == 503
    assert response.json()["detail"] == ("No API key for: azure_speech. Add it in Settings.")


def test_azure_uses_default_voice_for_piper_id(tmp_path, engine):
    settings = Settings(
        data_dir=tmp_path / "data",
        frontend_dist=tmp_path / "dist",
        azure_speech_key=SecretStr("azkey0123456789"),
        azure_speech_region="westeurope",
    )
    client = TestClient(create_app(settings))
    body = client.post(
        "/api/voice/speak", json={"text": QUESTION, "provider": "azure_speech"}
    ).json()
    assert body["voice"] == "pl-PL-ZofiaNeural"
    assert engine.calls[0]["voice"].voice == "pl-PL-ZofiaNeural"


def test_voices_list_marks_installed(client, settings):
    voices_dir = settings.effective_voices_dir
    voices_dir.mkdir(parents=True)
    (voices_dir / "pl_PL-gosia-medium.onnx").write_bytes(b"model")
    (voices_dir / "pl_PL-gosia-medium.onnx.json").write_text("{}", encoding="utf-8")
    voices = client.get("/api/voice/voices").json()
    by_id = {(v["provider"], v["id"]): v for v in voices}
    assert by_id[("piper", "pl_PL-gosia-medium")]["installed"] is True
    assert by_id[("piper", "pl_PL-gosia-medium")]["gender"] == "female"
    assert by_id[("piper", "pl_PL-darkman-medium")]["installed"] is False
    assert by_id[("piper", "pl_PL-mc_speech-medium")]["installed"] is False
    for azure in ("pl-PL-ZofiaNeural", "pl-PL-MarekNeural", "pl-PL-AgnieszkaNeural"):
        assert by_id[("azure_speech", azure)]["installed"] is False
    assert by_id[("browser", "browser")]["installed"] is True
    assert all(v["label"] for v in voices)


def test_voices_list_azure_installed_with_key_and_region(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        frontend_dist=tmp_path / "dist",
        azure_speech_key=SecretStr("azkey0123456789"),
        azure_speech_region="westeurope",
    )
    voices = service.list_voices(settings)
    assert all(v.installed for v in voices if v.provider == "azure_speech")


def test_preview_uses_sample_text(client, engine):
    response = client.post(
        "/api/voice/preview", json={"provider": "piper", "voice": "pl_PL-darkman-medium"}
    )
    assert response.status_code == 200
    assert response.json()["voice"] == "pl_PL-darkman-medium"
    assert (
        engine.calls[0]["text"]
        == service.SAMPLE_TEXT
        == ("Omów przyczyny i skutki rewolucji przemysłowej w Europie w XVIII i XIX wieku.")
    )


def test_cache_file_helper(settings, engine):
    result = service.speak(settings, QUESTION, provider="piper")
    assert service.cache_file(settings, result.cache_key).is_file()
    with pytest.raises(FileNotFoundError):
        service.cache_file(settings, "b" * 64)
    with pytest.raises(FileNotFoundError):
        service.cache_file(settings, "../etc")
