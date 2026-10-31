import logging
import traceback
import xml.etree.ElementTree as ET

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.features.providers import router as providers_router
from app.features.providers.base import Synthesizer
from app.features.providers.fakes import silent_wav
from app.features.providers.types import ProviderError, VoiceSpec
from app.features.voice.azure_adapter import (
    BAD_REGION_MESSAGE,
    OUTPUT_FORMAT,
    AzureSynthesizer,
    build_ssml,
    escape_ssml,
    rate_percent,
)
from app.main import create_app

KEY = "azkey0123456789abcdef0123456789ab"
SSML_NS = "{http://www.w3.org/2001/10/synthesis}"
VOICE = "pl-PL-ZofiaNeural"


def _synth(handler, region="westeurope", sleeps=None) -> AzureSynthesizer:
    return AzureSynthesizer(
        SecretStr(KEY),
        region,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
        sleep=(sleeps.append if sleeps is not None else lambda seconds: None),
    )


def _ok(request):
    return httpx2.Response(200, content=silent_wav(0.4, 24000), headers={"X-RequestId": "r-1"})


def _parse(body: bytes) -> ET.Element:
    root = ET.fromstring(body)
    assert root.tag == f"{SSML_NS}speak"
    return root


def _spoken_text(root: ET.Element) -> str:
    prosody = root.find(f"{SSML_NS}voice/{SSML_NS}prosody")
    return prosody.text


def test_request_url_headers_ssml():
    requests = []

    def handler(request):
        requests.append(request)
        return _ok(request)

    synth = _synth(handler)
    assert isinstance(synth, Synthesizer)
    result = synth.synthesize(
        "Co reguluje art. 415 Kodeksu cywilnego?",
        VoiceSpec(voice=VOICE, rate=1.1),
        operation_id="op-1",
    )
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://westeurope.tts.speech.microsoft.com/cognitiveservices/v1"
    assert request.headers["Ocp-Apim-Subscription-Key"] == KEY
    assert request.headers["Content-Type"] == "application/ssml+xml"
    assert (
        request.headers["X-Microsoft-OutputFormat"]
        == OUTPUT_FORMAT
        == ("riff-24khz-16bit-mono-pcm")
    )
    assert request.headers["User-Agent"] == "answeroncue"
    root = _parse(request.content)
    assert root.get("version") == "1.0"
    assert root.get("{http://www.w3.org/XML/1998/namespace}lang") == "pl-PL"
    voice = root.find(f"{SSML_NS}voice")
    assert voice.get("name") == VOICE
    prosody = voice.find(f"{SSML_NS}prosody")
    assert prosody.get("rate") == "+10%"
    assert prosody.text == "Co reguluje art. 415 Kodeksu cywilnego?"
    assert result.mime == "audio/wav"
    assert result.provenance.provider == "azure_speech"
    assert result.provenance.model == VOICE
    assert result.provenance.request_id == "r-1"
    assert result.usage.characters == len("Co reguluje art. 415 Kodeksu cywilnego?")
    assert result.usage.audio_seconds == pytest.approx(0.4, abs=0.01)


@pytest.mark.parametrize(
    "text",
    [
        "A < B & \"C\" ]]> 'D'",
        "<speak><voice name='x'>wstrzyknięcie</voice></speak>",
        "&amp; &lt; zostaje dosłownie",
        "<![CDATA[ x ]]>",
    ],
)
def test_escape_ssml_special_chars(text):
    body = build_ssml(text, VOICE, 1.0)
    assert _spoken_text(_parse(body.encode("utf-8"))) == text
    escaped = escape_ssml(text)
    assert "<" not in escaped and ">" not in escaped
    assert ET.fromstring(f"<t>{escaped}</t>").text == text


def test_voice_name_escaped_in_attribute():
    body = build_ssml("Pytanie?", 'pl-PL-Zofia"Neural', 1.0)
    voice = _parse(body.encode("utf-8")).find(f"{SSML_NS}voice")
    assert voice.get("name") == 'pl-PL-Zofia"Neural'


def test_xml_invalid_control_chars_do_not_break_ssml():
    body = build_ssml("Pytanie\x00 o\x0b PKB?", VOICE, 1.0)
    assert _spoken_text(_parse(body.encode("utf-8"))) == "Pytanie o PKB?"


def test_rate_percent():
    assert rate_percent(1.25) == "+25%"
    assert rate_percent(0.8) == "-20%"
    assert rate_percent(1.0) == "+0%"


def test_401_auth():
    synth = _synth(lambda request: httpx2.Response(401))
    with pytest.raises(ProviderError) as info:
        synth.synthesize("Pytanie?", VoiceSpec(voice=VOICE), operation_id="op")
    assert info.value.kind == "auth"
    assert info.value.provider == "azure_speech"
    assert info.value.message == "The provider rejected the key."
    assert info.value.status_code == 401


def test_429_retry_then_rate_limit():
    calls = []
    sleeps: list[float] = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(429, headers={"Retry-After": "60"})

    synth = _synth(handler, sleeps=sleeps)
    with pytest.raises(ProviderError) as info:
        synth.synthesize("Pytanie?", VoiceSpec(voice=VOICE), operation_id="op")
    assert len(calls) == 3
    assert sleeps == [5.0, 5.0]
    assert info.value.kind == "rate_limit"
    assert info.value.retryable is True
    assert info.value.message == (
        "The provider's request limit was exceeded. Try again in a moment."
    )


def test_5xx_retry_then_success():
    responses = iter([httpx2.Response(503, headers={"Retry-After": "1"}), None])
    sleeps: list[float] = []

    def handler(request):
        response = next(responses)
        return response if response is not None else _ok(request)

    result = _synth(handler, sleeps=sleeps).synthesize(
        "Pytanie?", VoiceSpec(voice=VOICE), operation_id="op"
    )
    assert result.mime == "audio/wav"
    assert sleeps == [1.0]


@pytest.mark.parametrize(
    ("raised", "kind"),
    [(httpx2.ConnectError, "network"), (httpx2.ReadTimeout, "timeout")],
)
def test_network_errors(raised, kind):
    def handler(request):
        raise raised("boom", request=request)

    with pytest.raises(ProviderError) as info:
        _synth(handler).synthesize("Pytanie?", VoiceSpec(voice=VOICE), operation_id="op")
    assert info.value.kind == kind


@pytest.mark.parametrize("region", ["West Europe", "westeurope.evil.com/x", "", "WESTEUROPE"])
def test_bad_region_no_http_call(region):
    calls = []

    def handler(request):
        calls.append(request)
        return _ok(request)

    with pytest.raises(ProviderError) as info:
        _synth(handler, region=region).synthesize(
            "Pytanie?", VoiceSpec(voice=VOICE), operation_id="op"
        )
    assert info.value.kind == "bad_request"
    assert info.value.message == BAD_REGION_MESSAGE == "Invalid Azure region."
    assert calls == []


def test_key_not_in_errors_or_logs(caplog):
    def handler(request):
        echoed = request.headers["Ocp-Apim-Subscription-Key"]
        return httpx2.Response(
            401, text=f"bad key {echoed}", headers={"X-Echo": echoed, "X-RequestId": "r"}
        )

    caplog.set_level(logging.DEBUG)
    with pytest.raises(ProviderError) as info:
        _synth(handler).synthesize("Pytanie?", VoiceSpec(voice=VOICE), operation_id="op")
    error = info.value
    rendered = "".join(traceback.format_exception(error))
    assert KEY not in rendered
    assert KEY not in repr(error)
    assert KEY not in caplog.text


def _key_test_client(tmp_path, monkeypatch, handler, region="westeurope"):
    monkeypatch.setattr(
        providers_router,
        "http_client_factory",
        lambda: httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        frontend_dist=tmp_path / "dist",
        env_file_path=tmp_path / ".env",
        azure_speech_key=SecretStr(KEY),
        azure_speech_region=region,
    )
    return TestClient(create_app(settings))


def test_key_test_azure_ok(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(200, json=[{"ShortName": VOICE}])

    client = _key_test_client(tmp_path, monkeypatch, handler)
    response = client.post("/api/providers/keys/azure_speech/test")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "error_kind": None, "message": "The key works."}
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url) == (
        "https://westeurope.tts.speech.microsoft.com/cognitiveservices/voices/list"
    )
    assert request.headers["Ocp-Apim-Subscription-Key"] == KEY
    assert request.headers["User-Agent"] == "answeroncue"


def test_key_test_azure_auth_failure(tmp_path, monkeypatch):
    def handler(request):
        return httpx2.Response(401, text=f"denied {KEY}")

    client = _key_test_client(tmp_path, monkeypatch, handler)
    response = client.post("/api/providers/keys/azure_speech/test")
    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "error_kind": "auth",
        "message": "The provider rejected the key.",
    }
    assert KEY not in response.text


@pytest.mark.parametrize(
    ("raised", "kind"),
    [(httpx2.ConnectError, "network"), (httpx2.ConnectTimeout, "timeout")],
)
def test_key_test_azure_network_failure(tmp_path, monkeypatch, raised, kind):
    def handler(request):
        raise raised("down", request=request)

    client = _key_test_client(tmp_path, monkeypatch, handler)
    body = client.post("/api/providers/keys/azure_speech/test").json()
    assert body["ok"] is False
    assert body["error_kind"] == kind


def test_key_test_azure_bad_region_no_http_call(tmp_path, monkeypatch):
    calls = []
    client = _key_test_client(
        tmp_path, monkeypatch, lambda request: calls.append(request), region="west europe"
    )
    body = client.post("/api/providers/keys/azure_speech/test").json()
    assert body == {
        "ok": False,
        "error_kind": "bad_request",
        "message": "Invalid Azure region.",
    }
    assert calls == []
