import json

import httpx2
import pytest

from app.features.voice import download
from app.features.voice.download import (
    KNOWN_VOICES,
    DownloadError,
    download_voice,
    voice_urls,
)
from app.features.voice.piper_adapter import PiperSynthesizer

BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/pl/pl_PL"
MODEL = b"\x08" * 1_100_000
CONFIG = json.dumps({"audio": {"sample_rate": 22050}}).encode()


def _client(model: bytes = MODEL, config: bytes = CONFIG, requests: list | None = None):
    def handler(request):
        if requests is not None:
            requests.append(str(request.url))
        if str(request.url).endswith(".onnx.json"):
            return httpx2.Response(200, content=config)
        return httpx2.Response(200, content=model)

    return httpx2.Client(transport=httpx2.MockTransport(handler))


def test_known_voices():
    assert KNOWN_VOICES == (
        "pl_PL-gosia-medium",
        "pl_PL-darkman-medium",
        "pl_PL-mc_speech-medium",
        "pl_PL-bass-high",
    )


def test_url_for_high_quality_voice():
    assert voice_urls("pl_PL-bass-high")[0] == f"{BASE}/bass/high/pl_PL-bass-high.onnx"


def test_url_for_voice():
    assert voice_urls("pl_PL-gosia-medium") == (
        f"{BASE}/gosia/medium/pl_PL-gosia-medium.onnx",
        f"{BASE}/gosia/medium/pl_PL-gosia-medium.onnx.json",
    )
    assert voice_urls("pl_PL-mc_speech-medium")[0] == (
        f"{BASE}/mc_speech/medium/pl_PL-mc_speech-medium.onnx"
    )


def test_download_atomic_and_validated(tmp_path):
    requests: list[str] = []
    dest = tmp_path / "voices"
    path = download_voice("pl_PL-gosia-medium", dest, http_client=_client(requests=requests))
    assert path == dest / "pl_PL-gosia-medium.onnx"
    assert path.read_bytes() == MODEL
    assert (dest / "pl_PL-gosia-medium.onnx.json").read_bytes() == CONFIG
    assert sorted(p.name for p in dest.iterdir()) == [
        "pl_PL-gosia-medium.onnx",
        "pl_PL-gosia-medium.onnx.json",
    ]
    assert set(requests) == set(voice_urls("pl_PL-gosia-medium"))
    assert PiperSynthesizer(dest).is_installed("pl_PL-gosia-medium")


def test_truncated_model_rejected(tmp_path):
    dest = tmp_path / "voices"
    with pytest.raises(DownloadError):
        download_voice("pl_PL-gosia-medium", dest, http_client=_client(model=b"x" * 1000))
    assert list(dest.iterdir()) == []


def test_bad_config_rejected(tmp_path):
    dest = tmp_path / "voices"
    with pytest.raises(DownloadError):
        download_voice("pl_PL-gosia-medium", dest, http_client=_client(config=b"<html>"))
    assert list(dest.iterdir()) == []


def test_http_error_keeps_existing_model(tmp_path):
    dest = tmp_path / "voices"
    download_voice("pl_PL-gosia-medium", dest, http_client=_client())

    def handler(request):
        return httpx2.Response(404)

    broken = httpx2.Client(transport=httpx2.MockTransport(handler))
    with pytest.raises(DownloadError) as info:
        download_voice("pl_PL-gosia-medium", dest, http_client=broken)
    assert "pl_PL-gosia-medium" in str(info.value)
    assert (dest / "pl_PL-gosia-medium.onnx").read_bytes() == MODEL
    assert len(list(dest.iterdir())) == 2


def test_network_error(tmp_path):
    def handler(request):
        raise httpx2.ConnectError("down", request=request)

    with pytest.raises(DownloadError) as info:
        download_voice(
            "pl_PL-gosia-medium",
            tmp_path,
            http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
        )
    assert str(info.value).startswith("Could not download the voice")


@pytest.mark.parametrize("voice", ["pl_PL-nieznany-medium", "en_US-amy-medium", "../x"])
def test_unknown_voice_rejected(tmp_path, voice):
    with pytest.raises(DownloadError) as info:
        download_voice(voice, tmp_path, http_client=_client())
    assert "Unknown Piper voice" in str(info.value)
    with pytest.raises(DownloadError):
        voice_urls(voice)


def test_cli_downloads_all_known_by_default(tmp_path, monkeypatch, capsys):
    calls: list[str] = []

    def fake_download(voice, dest_dir, *, http_client=None):
        calls.append(voice)
        return dest_dir / f"{voice}.onnx"

    monkeypatch.setattr(download, "download_voice", fake_download)
    assert download.main([], dest_dir=tmp_path) == 0
    assert calls == list(KNOWN_VOICES)
    assert download.main(["pl_PL-darkman-medium"], dest_dir=tmp_path) == 0
    assert calls[-1] == "pl_PL-darkman-medium"


def test_cli_reports_failure(tmp_path, monkeypatch, capsys):
    def failing(voice, dest_dir, *, http_client=None):
        raise DownloadError(f"Could not download the voice {voice}.")

    monkeypatch.setattr(download, "download_voice", failing)
    assert download.main(["pl_PL-gosia-medium"], dest_dir=tmp_path) == 1
    assert "Could not download the voice pl_PL-gosia-medium" in capsys.readouterr().out
