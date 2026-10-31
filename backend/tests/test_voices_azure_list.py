import json
import logging
import os
import time

import httpx2
import pytest
from pydantic import SecretStr

from app.features.languages.profiles import PROFILES
from app.features.providers.fakes import silent_wav
from app.features.providers.types import ProviderError, VoiceSpec
from app.features.voice import azure_voices
from app.features.voice.azure_adapter import UNKNOWN_AZURE_VOICE, AzureSynthesizer, build_ssml
from app.features.voice.azure_voices import PINNED, cache_path, refresh, voices_for
from app.features.voice.catalog import AZURE_VOICES

KEY = "az-test-0123456789"
LIST_URL = "https://westeurope.tts.speech.microsoft.com/cognitiveservices/voices/list"

FACTS = {
    "pl-PL": ["pl-PL-ZofiaNeural", "pl-PL-MarekNeural", "pl-PL-AgnieszkaNeural"],
    "en-GB": ["en-GB-SoniaNeural", "en-GB-RyanNeural", "en-GB-LibbyNeural"],
    "de-DE": ["de-DE-KatjaNeural", "de-DE-ConradNeural", "de-DE-AmalaNeural"],
    "fr-FR": ["fr-FR-DeniseNeural", "fr-FR-HenriNeural", "fr-FR-EloiseNeural"],
    "es-ES": ["es-ES-ElviraNeural", "es-ES-AlvaroNeural", "es-ES-AbrilNeural"],
    "it-IT": ["it-IT-ElsaNeural", "it-IT-DiegoNeural", "it-IT-IsabellaNeural"],
    "uk-UA": ["uk-UA-PolinaNeural", "uk-UA-OstapNeural"],
}

AZURE_LIST = [
    {
        "ShortName": "de-DE-KatjaNeural",
        "DisplayName": "Katja",
        "LocalName": "Katja",
        "Gender": "Female",
        "Locale": "de-DE",
        "VoiceType": "Neural",
        "Status": "GA",
    },
    {
        "ShortName": "de-DE-SeraphinaMultilingualNeural",
        "DisplayName": "Seraphina Multilingual",
        "LocalName": "Seraphina Multilingual",
        "Gender": "Female",
        "Locale": "de-DE",
        "VoiceType": "Neural",
        "Status": "GA",
    },
    {
        "ShortName": "en-GB-SoniaNeural",
        "DisplayName": "Sonia",
        "LocalName": "Sonia",
        "Gender": "Female",
        "Locale": "en-GB",
        "VoiceType": "Neural",
        "Status": "GA",
    },
    {
        "ShortName": "de-DE-Hedda",
        "DisplayName": "Hedda",
        "LocalName": "Hedda",
        "Gender": "Female",
        "Locale": "de-DE",
        "VoiceType": "Standard",
        "Status": "GA",
    },
]


@pytest.fixture
def azure(settings):
    return settings.model_copy(
        update={"azure_speech_key": SecretStr(KEY), "azure_speech_region": "westeurope"}
    )


def client(handler) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(handler))


def listing(requests: list | None = None, body=AZURE_LIST):
    def handler(request):
        if requests is not None:
            requests.append(request)
        return httpx2.Response(200, json=body)

    return client(handler)


def test_pinned_per_locale():
    assert set(PINNED) == {profile.azure_locale for profile in PROFILES.values()}
    assert {locale: [v.short_name for v in voices] for locale, voices in PINNED.items()} == FACTS
    assert {v.short_name: (v.label, v.gender) for v in PINNED["pl-PL"]} == AZURE_VOICES
    assert list(AZURE_VOICES) == FACTS["pl-PL"]
    sonia, ryan, _ = PINNED["en-GB"]
    assert (sonia.label, sonia.gender, sonia.locale) == ("Sonia (Azure, neural)", "female", "en-GB")
    assert ryan.gender == "male"


def test_voices_for_without_cache(settings, azure):
    for current in (settings, azure):
        voices = voices_for(current, PROFILES["de"])
        assert [v.short_name for v in voices] == FACTS["de-DE"]


def test_refresh_with_key(azure):
    requests = []
    assert refresh(azure, http_client=listing(requests)) is True
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url) == LIST_URL
    assert request.headers["Ocp-Apim-Subscription-Key"] == KEY
    path = cache_path(azure)
    assert path == azure.effective_voices_dir / "azure-voices-westeurope.json"
    text = path.read_text(encoding="utf-8")
    assert KEY not in text
    assert "de-DE-Hedda" not in text
    german = voices_for(azure, PROFILES["de"])
    assert [(v.short_name, v.label, v.gender) for v in german] == [
        ("de-DE-KatjaNeural", "Katja (Azure, neural)", "female"),
        ("de-DE-SeraphinaMultilingualNeural", "Seraphina Multilingual (Azure, neural)", "female"),
    ]
    # A locale the region's list does not hold keeps the pinned voices.
    assert [v.short_name for v in voices_for(azure, PROFILES["fr"])] == FACTS["fr-FR"]
    # Fresh for 7 days: no second request.
    assert refresh(azure, http_client=listing(requests)) is False
    assert len(requests) == 1
    stamp = time.time() - 8 * 86400
    os.utime(path, (stamp, stamp))
    assert refresh(azure, http_client=listing(requests)) is True
    assert len(requests) == 2


def test_refresh_survives_an_odd_gender(azure):
    body = [{**AZURE_LIST[0], "Gender": ["Female"]}]
    assert refresh(azure, http_client=listing(body=body)) is True
    (katja,) = voices_for(azure, PROFILES["de"])[:1]
    assert (katja.short_name, katja.gender) == ("de-DE-KatjaNeural", None)


@pytest.mark.parametrize(
    "update",
    [
        {"azure_speech_region": "westeurope"},
        {"azure_speech_key": SecretStr(KEY)},
        {"azure_speech_key": SecretStr(KEY), "azure_speech_region": "west europe/../x"},
    ],
    ids=["no-key", "no-region", "bad-region"],
)
def test_refresh_without_key_makes_no_request(settings, tmp_path, monkeypatch, update):
    calls = []

    def counting():
        calls.append(1)
        return None

    monkeypatch.setattr(azure_voices, "http_client_factory", counting)
    current = settings.model_copy(update=update)
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    assert refresh(current) is False
    assert calls == []
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before
    if "bad-region" in str(update):
        assert cache_path(current) is None


def _status(code):
    return lambda request: httpx2.Response(code, text=f"denied {KEY}")


def _invalid(request):
    return httpx2.Response(200, text="<html>")


@pytest.mark.parametrize(
    "handler", [_status(401), _status(500), _invalid], ids=["401", "500", "json"]
)
def test_refresh_errors_keep_the_cache(azure, caplog, handler):
    assert refresh(azure, http_client=listing()) is True
    path = cache_path(azure)
    old = path.read_bytes()
    stamp = time.time() - 30 * 86400
    os.utime(path, (stamp, stamp))
    with caplog.at_level(logging.DEBUG):
        assert refresh(azure, http_client=client(handler)) is False
    assert path.read_bytes() == old
    assert KEY not in caplog.text
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_bad_cache_is_ignored(azure):
    path = cache_path(azure)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"voices": [{"short_name": "x"}]}), encoding="utf-8")
    assert [v.short_name for v in voices_for(azure, PROFILES["de"])] == FACTS["de-DE"]


def test_ssml_language_follows_the_voice():
    assert 'xml:lang="de-DE"' in build_ssml("Hallo", "de-DE-KatjaNeural", 1.0)
    polish = build_ssml("Cześć", "pl-PL-ZofiaNeural", 1.0)
    assert 'xml:lang="pl-PL"' in polish
    assert polish.startswith(
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="pl-PL">'
    )
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(200, content=silent_wav(0.4, 24000))

    synth = AzureSynthesizer(SecretStr(KEY), "westeurope", http_client=client(handler))
    with pytest.raises(ProviderError) as info:
        synth.synthesize("Hallo", VoiceSpec(voice="Katja"), operation_id="op")
    assert info.value.kind == "bad_request"
    assert info.value.message == UNKNOWN_AZURE_VOICE
    assert requests == []
