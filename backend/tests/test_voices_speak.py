"""speak, preview, the voices list and the trial, per content language."""

import inspect

import httpx2
import pytest
from conftest import local_client
from pydantic import SecretStr
from test_costs_support import usage_rows
from test_rounds_api import start
from test_rounds_support import make_world
from test_topics_support import upgrade
from test_voice_api import FakeEngine, engine  # noqa: F401
from test_voice_openai_service import checking, keyed, server  # noqa: F401
from test_voices_azure_list import AZURE_LIST
from test_voices_piper_catalog import entry_json, full_list

from app.db.session import SessionLocal
from app.features.languages.profiles import PROFILES
from app.features.voice import azure_voices, piper_catalog, service, trial
from app.features.voice.catalog import AZURE_VOICES, OPENAI_VOICES, PIPER_VOICES
from app.features.voice.per_language import VOICE_WRONG_LANGUAGE
from app.features.voice.service import NEED_LANGUAGE, OPENAI_NEEDS_LANGUAGE
from app.main import create_app

GERMAN = "Erklären Sie die Ursachen der industriellen Revolution."
POLISH = "Wyjaśnij różnicę między PKB a PNB i podaj przykład."
AZURE_KEY = "az-test-0123456789"


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def azure(settings):
    return settings.model_copy(
        update={"azure_speech_key": SecretStr(AZURE_KEY), "azure_speech_region": "westeurope"}
    )


@pytest.fixture
def counters(monkeypatch):
    calls = {"piper": 0, "azure": 0}

    def counting(name):
        def factory():
            calls[name] += 1
            return None

        return factory

    monkeypatch.setattr(piper_catalog, "http_client_factory", counting("piper"))
    monkeypatch.setattr(azure_voices, "http_client_factory", counting("azure"))
    return calls


def install(settings, voice: str) -> None:
    folder = settings.effective_voices_dir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{voice}.onnx").write_bytes(b"model")
    (folder / f"{voice}.onnx.json").write_text("{}", encoding="utf-8")


def speak(client, **body):
    return client.post("/api/voice/speak", json={"text": GERMAN, **body})


def test_language_resolves_the_voice(client, engine, settings, db):  # noqa: F811
    install(settings, "pl_PL-bass-high")
    response = speak(client, language="de")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["provider"], body["voice"], body["audio_url"]) == ("browser", "browser", None)
    assert engine.calls == []
    install(settings, "de_DE-thorsten-high")
    body = speak(client, language="de").json()
    assert (body["provider"], body["voice"]) == ("piper", "de_DE-thorsten-high")
    assert [call["voice"].voice for call in engine.calls] == ["de_DE-thorsten-high"]


def test_wrong_language_refused(client, engine, db):  # noqa: F811
    response = speak(client, language="de", provider="piper", voice="pl_PL-bass-high")
    assert response.status_code == 422
    assert response.json()["detail"] == VOICE_WRONG_LANGUAGE
    response = speak(client, language="en", provider="azure_speech", voice="pl-PL-ZofiaNeural")
    assert response.status_code == 422
    assert response.json()["detail"] == VOICE_WRONG_LANGUAGE
    assert engine.calls == []


def test_provider_without_voice_uses_the_language_default(azure, engine):  # noqa: F811
    upgrade(azure.effective_database_url)
    client = local_client(create_app(azure))
    body = speak(client, text="Explain it.", language="en", provider="azure_speech").json()
    assert (body["provider"], body["voice"]) == ("azure_speech", "en-GB-SoniaNeural")
    assert engine.calls[0]["voice"].voice == "en-GB-SoniaNeural"


def test_no_provider_no_language(client, engine):  # noqa: F811
    response = client.post("/api/voice/speak", json={"text": "x"})
    assert response.status_code == 422
    assert response.json()["detail"] == NEED_LANGUAGE
    response = client.post("/api/voice/speak", json={"text": "x", "language": "xx"})
    assert response.status_code == 422
    assert response.json()["detail"] == "Unknown language."
    assert engine.calls == []


def test_legacy_explicit_provider_unchanged(client, engine):  # noqa: F811
    body = client.post("/api/voice/speak", json={"text": POLISH, "provider": "piper"}).json()
    assert (body["provider"], body["voice"]) == ("piper", "pl_PL-bass-high")


def test_openai_needs_a_language(keyed, server):  # noqa: F811
    client = local_client(create_app(keyed))
    response = client.post(
        "/api/voice/speak", json={"text": POLISH, "provider": "openai", "voice": "marin"}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == OPENAI_NEEDS_LANGUAGE
    assert server.connections == 0
    assert usage_rows(keyed.effective_database_url) == []


def _instructions(server) -> list[str]:  # noqa: F811 - the fixture's name
    return [
        event["session"]["instructions"]
        for events in server.events
        for event in events
        if event.get("type") == "session.update"
    ]


def test_openai_check_uses_the_subject_language(keyed, server, monkeypatch):  # noqa: F811
    heard = checking(monkeypatch, [GERMAN])
    service.speak(keyed, GERMAN, language="de", provider="openai")
    assert [call["language"] for call in heard.calls] == ["de"]
    assert "word for word, in German." in _instructions(server)[0]

    heard = checking(monkeypatch, [POLISH])
    service.speak(keyed, POLISH, language="pl", provider="openai")
    assert [call["language"] for call in heard.calls] == ["pl"]
    assert "word for word, in Polish." in _instructions(server)[-1]


def test_the_t33_default_is_gone(keyed):  # noqa: F811
    assert "# T33" not in inspect.getsource(service)
    with pytest.raises(TypeError):
        service._read_checked(keyed, FakeEngine(), POLISH, None, "a" * 64, language_name="Polish")


def test_preview_sentence_per_language(client, engine):  # noqa: F811
    german = {"provider": "piper", "voice": "de_DE-thorsten-high"}
    assert client.post("/api/voice/preview", json={**german, "language": "de"}).status_code == 200
    # Another rate, so the second preview is not the cached first one.
    assert client.post("/api/voice/preview", json={**german, "rate": 1.1}).status_code == 200
    assert [call["text"] for call in engine.calls] == [PROFILES["de"].preview_sentence] * 2

    wrong = {"provider": "piper", "voice": "pl_PL-bass-high", "language": "de"}
    response = client.post("/api/voice/preview", json=wrong)
    assert response.status_code == 422
    assert response.json()["detail"] == VOICE_WRONG_LANGUAGE
    assert len(engine.calls) == 2


def test_openai_preview_is_polish_without_a_language(keyed, server, monkeypatch):  # noqa: F811
    polish = PROFILES["pl"].preview_sentence
    heard = checking(monkeypatch, [polish])
    openai = local_client(create_app(keyed))
    response = openai.post("/api/voice/preview", json={"provider": "openai", "voice": "marin"})
    assert response.status_code == 200, response.text
    assert [call["language"] for call in heard.calls] == ["pl"]
    assert "word for word, in Polish." in _instructions(server)[0]


def _listed(settings, query: str = "") -> list[dict]:
    response = local_client(create_app(settings)).get(f"/api/voice/voices{query}")
    assert response.status_code == 200, response.text
    return response.json()


def test_voices_for_a_language(settings, azure):
    voices = _listed(settings, "?language=de")
    piper = [v for v in voices if v["provider"] == "piper"]
    assert [(v["id"], v["installed"], v["language"]) for v in piper] == [
        ("de_DE-thorsten-high", False, "de")
    ]
    assert piper[0]["label"] == "Thorsten (Piper, local, high quality)"
    assert piper[0]["gender"] is None
    assert not [v for v in voices if v["provider"] == "azure_speech"]
    openai = [v for v in voices if v["provider"] == "openai"]
    assert [v["id"] for v in openai] == list(OPENAI_VOICES)
    assert all(v["language"] is None for v in openai)
    assert voices[-1]["provider"] == "browser" and voices[-1]["language"] is None
    assert not [v for v in voices if v["id"].startswith(("pl_PL-", "pl-PL-"))]

    install(azure, "de_DE-karlsson-low")
    voices = _listed(azure, "?language=de")
    piper = [v for v in voices if v["provider"] == "piper"]
    assert [(v["id"], v["installed"]) for v in piper] == [
        ("de_DE-thorsten-high", False),
        ("de_DE-karlsson-low", True),
    ]
    azure_listed = [v for v in voices if v["provider"] == "azure_speech"]
    assert [(v["id"], v["installed"], v["language"]) for v in azure_listed] == [
        ("de-DE-KatjaNeural", True, "de"),
        ("de-DE-ConradNeural", True, "de"),
        ("de-DE-AmalaNeural", True, "de"),
    ]
    assert not [v for v in voices if v["id"].startswith(("pl_PL-", "pl-PL-"))]


def test_voices_without_language_is_polish(settings, azure):
    expected = (
        [("piper", key) for key in PIPER_VOICES]
        + [("azure_speech", key) for key in AZURE_VOICES]
        + [("openai", key) for key in OPENAI_VOICES]
        + [("browser", "browser")]
    )
    for current in (settings, azure):
        voices = _listed(current)
        assert [(v["provider"], v["id"]) for v in voices] == expected
        labels = {v["id"]: (v["label"], v["gender"]) for v in voices}
        assert {key: labels[key] for key in PIPER_VOICES} == PIPER_VOICES
        assert {key: labels[key] for key in AZURE_VOICES} == AZURE_VOICES
        for voice in voices:
            local = voice["provider"] in ("piper", "azure_speech")
            assert voice["language"] == ("pl" if local else None)
    assert {v["installed"] for v in _listed(settings) if v["provider"] == "azure_speech"} == {False}
    assert {v["installed"] for v in _listed(azure) if v["provider"] == "azure_speech"} == {True}
    assert _listed(settings, "?language=pl") == [
        v for v in _listed(settings) if v["provider"] != "azure_speech"
    ]


def test_refresh_only_when_asked(azure, counters, engine, monkeypatch):  # noqa: F811
    upgrade(azure.effective_database_url)
    client = local_client(create_app(azure))
    assert client.get("/api/voice/voices?language=en").status_code == 200
    assert speak(client, text="Explain it.", language="en", provider="piper").status_code == 200
    preview = {"provider": "piper", "voice": "en_GB-cori-high"}
    assert client.post("/api/voice/preview", json=preview).status_code == 200
    with SessionLocal(azure.effective_database_url) as db:
        world = make_world(db)
    start(client, world, question_count=1, question_audio={"mode": "premium"})
    assert counters == {"piper": 0, "azure": 0}

    requests = []

    def handler(request):
        requests.append(str(request.url))
        if request.url.host == "huggingface.co":
            return httpx2.Response(200, text=full_list(entry_json("en_GB-alba-medium")))
        return httpx2.Response(200, json=AZURE_LIST)

    fake = lambda: httpx2.Client(transport=httpx2.MockTransport(handler))  # noqa: E731
    monkeypatch.setattr(piper_catalog, "http_client_factory", fake)
    monkeypatch.setattr(azure_voices, "http_client_factory", fake)
    voices = client.get("/api/voice/voices?language=en&refresh=true").json()
    assert sorted(requests) == [
        piper_catalog.VOICES_JSON_URL,
        "https://westeurope.tts.speech.microsoft.com/cognitiveservices/voices/list",
    ]
    assert "en_GB-alba-medium" in [v["id"] for v in voices]
    assert [v["id"] for v in voices if v["provider"] == "azure_speech"] == ["en-GB-SoniaNeural"]
    client.get("/api/voice/voices?language=en&refresh=true")
    assert len(requests) == 2


def test_refresh_without_azure_key_fetches_only_piper(settings, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request.url.host)
        return httpx2.Response(200, text=full_list())

    fake = lambda: httpx2.Client(transport=httpx2.MockTransport(handler))  # noqa: E731
    monkeypatch.setattr(piper_catalog, "http_client_factory", fake)
    monkeypatch.setattr(azure_voices, "http_client_factory", fake)
    assert _listed(settings, "?language=fr&refresh=true")
    assert requests == ["huggingface.co"]


def test_unknown_language_in_the_list(client):
    response = client.get("/api/voice/voices?language=xx")
    assert response.status_code == 422
    assert response.json()["detail"] == "Unknown language."


def test_trial_per_language(settings, azure, keyed, engine, monkeypatch, tmp_path):  # noqa: F811
    install(settings, "de_DE-thorsten-high")
    install(settings, "pl_PL-bass-high")
    assert trial.available_voices(settings, "de") == [("piper", "de_DE-thorsten-high")]
    install(azure, "de_DE-thorsten-high")
    assert trial.available_voices(azure, "de") == [
        ("piper", "de_DE-thorsten-high"),
        ("azure_speech", "de-DE-KatjaNeural"),
        ("azure_speech", "de-DE-ConradNeural"),
        ("azure_speech", "de-DE-AmalaNeural"),
    ]
    assert [v for v in trial.available_voices(keyed, "de") if v[0] == "openai"] == [
        ("openai", "marin")
    ]

    out = tmp_path / "de"
    page = trial.render_trial(
        settings, out, [("piper", "de_DE-thorsten-high")], language="de"
    ).read_text(encoding="utf-8")
    assert [call["text"] for call in engine.calls] == list(PROFILES["de"].trial_sentences)
    assert page.count("<audio") == 4
    assert "German" in page
    assert "ntonation" in page
    assert "Grunwald" not in page

    asked = []

    def voices(current, language="pl"):
        asked.append(language)
        prefix = PROFILES[language].piper_prefix
        return [("piper", f"{prefix}-x-medium")]

    monkeypatch.setattr(trial, "get_settings", lambda: settings)
    monkeypatch.setattr(trial, "available_voices", voices)
    engine.calls.clear()
    assert trial.main(["--language", "it", str(tmp_path / "it")]) == 0
    assert [call["text"] for call in engine.calls] == list(PROFILES["it"].trial_sentences)
    engine.calls.clear()
    assert trial.main([]) == 0
    assert asked == ["it", "pl"]
    assert len(engine.calls) == 12
    assert [call["text"] for call in engine.calls] == list(trial.SAMPLE_QUESTIONS)
    assert (settings.data_dir / "voice-trial" / "index.html").is_file()
