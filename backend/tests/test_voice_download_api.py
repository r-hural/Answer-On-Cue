import json
import threading
import time

import httpx2
import pytest

from app.features.voice import download, downloads
from app.features.voice.download import DownloadError, download_voice, main

VOICE = "pl_PL-gosia-medium"
MODEL = b"\x08" * 1_100_000
CONFIG = json.dumps({"audio": {"sample_rate": 22050}}).encode()


def transport(model: bytes = MODEL, status: int = 200, gate: threading.Event | None = None):
    def handler(request):
        if gate is not None:
            gate.wait(5)
        if status != 200:
            return httpx2.Response(status)
        if str(request.url).endswith(".onnx.json"):
            return httpx2.Response(200, content=CONFIG)
        return httpx2.Response(200, content=model)

    return httpx2.MockTransport(handler)


@pytest.fixture(autouse=True)
def _reset_states():
    downloads._states.clear()
    yield
    downloads._states.clear()


def use(monkeypatch, mock) -> None:
    monkeypatch.setattr(download, "http_client_factory", lambda: httpx2.Client(transport=mock))


def wait_for(client, voice=VOICE, until=("done", "failed")):
    for _ in range(200):
        state = client.get(f"/api/voice/downloads/{voice}").json()
        if state["status"] in until:
            return state
        time.sleep(0.02)
    raise AssertionError(f"still {state}")


def test_progress_reported_to_done(client, monkeypatch, settings):
    gate = threading.Event()
    use(monkeypatch, transport(gate=gate))
    started = client.post("/api/voice/downloads", json={"voice": VOICE})
    assert started.status_code == 202
    assert started.json()["status"] == "running"
    assert client.get(f"/api/voice/downloads/{VOICE}").json()["status"] == "running"
    gate.set()
    state = wait_for(client)
    assert state["status"] == "done" and state["error"] is None
    assert state["bytes_done"] == len(MODEL) + len(CONFIG)
    assert state["bytes_total"] == len(MODEL) + len(CONFIG)
    assert (settings.effective_voices_dir / f"{VOICE}.onnx").stat().st_size == len(MODEL)


def test_progress_callback_grows_with_known_total(tmp_path):
    seen: list[tuple[int, int | None]] = []
    download_voice(
        VOICE,
        tmp_path,
        http_client=httpx2.Client(transport=transport()),
        progress=lambda done, total: seen.append((done, total)),
    )
    done = [d for d, _ in seen]
    assert done == sorted(done) and done[-1] == len(MODEL) + len(CONFIG)
    assert seen[-1][1] == len(MODEL) + len(CONFIG)
    assert any(t == len(MODEL) for _, t in seen)  # known as soon as the model responds


def test_failed_download_state(client, monkeypatch):
    use(monkeypatch, transport(status=404))
    client.post("/api/voice/downloads", json={"voice": VOICE})
    state = wait_for(client)
    assert state["status"] == "failed"
    assert (
        state["error"] == f"Could not download the voice {VOICE}: the server replied with code 404."
    )
    use(monkeypatch, transport())
    client.post("/api/voice/downloads", json={"voice": VOICE})
    assert wait_for(client)["status"] == "done"


def test_unknown_voice_422(client):
    response = client.post("/api/voice/downloads", json={"voice": "pl_PL-nikt-medium"})
    assert response.status_code == 422
    assert "Unknown Piper voice" in response.json()["detail"]


def test_second_start_joins_running(client, monkeypatch):
    gate = threading.Event()
    requests: list[str] = []
    mock = transport(gate=gate)

    def counting(request):
        requests.append(str(request.url))
        return mock.handle_request(request)

    use(monkeypatch, httpx2.MockTransport(counting))
    first = client.post("/api/voice/downloads", json={"voice": VOICE})
    second = client.post("/api/voice/downloads", json={"voice": VOICE})
    assert first.json()["status"] == second.json()["status"] == "running"
    gate.set()
    assert wait_for(client)["status"] == "done"
    assert len(requests) == 2  # one model and one config request, not two downloads


def test_installed_voice_reports_done(client, settings):
    assert client.get(f"/api/voice/downloads/{VOICE}").json()["status"] == "idle"
    settings.effective_voices_dir.mkdir(parents=True)
    (settings.effective_voices_dir / f"{VOICE}.onnx").write_bytes(MODEL)
    (settings.effective_voices_dir / f"{VOICE}.onnx.json").write_bytes(CONFIG)
    assert client.get(f"/api/voice/downloads/{VOICE}").json()["status"] == "done"


def test_cli_unchanged(tmp_path, monkeypatch, capsys):
    use(monkeypatch, transport())
    assert main([VOICE], dest_dir=tmp_path) == 0
    assert (tmp_path / f"{VOICE}.onnx").read_bytes() == MODEL
    assert "Done" in capsys.readouterr().out
    use(monkeypatch, transport(status=500))
    with pytest.raises(DownloadError):
        download_voice("pl_PL-darkman-medium", tmp_path)
