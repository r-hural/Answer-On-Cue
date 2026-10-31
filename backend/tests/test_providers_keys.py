import httpx2
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.features.providers import router as providers_router
from app.features.providers.keys import mask, read_env, remove_env_value, set_env_value
from app.features.providers.registry import get_text_generator
from app.features.providers.types import ProviderError
from app.main import create_app
from tests.conftest import local_client

FAKE_KEY = "sk-test-0000000000abcd"
KEY_ENV = (
    "AOC_OPENAI_API_KEY",
    "AOC_ANTHROPIC_API_KEY",
    "AOC_AZURE_SPEECH_KEY",
    "AOC_AZURE_SPEECH_REGION",
    "AOC_GENERATION_PROVIDER",
    "AOC_GENERATION_MODEL",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in KEY_ENV:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def env_path(tmp_path):
    return tmp_path / ".env"


@pytest.fixture
def settings(tmp_path, env_path) -> Settings:
    return Settings(
        _env_file=env_path,
        data_dir=tmp_path / "data",
        frontend_dist=tmp_path / "dist",
        env_file_path=env_path,
        base_env_file=None,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app) -> TestClient:
    return local_client(app)


def test_set_preserves_other_lines(env_path):
    original = (
        b"# Ustawienia lokalne\r\n"
        b"AOC_PORT=9000\r\n"
        b"\r\n"
        b"  # wci\xc4\x99ty komentarz = z # znakami\r\n"
        b"AOC_AZURE_SPEECH_REGION=westeurope\r\n"
        b"AOC_OPENAI_API_KEY=sk-test-old0000000000\r\n"
        b"AOC_HOST=127.0.0.1"
    )
    env_path.write_bytes(original)
    set_env_value(env_path, "AOC_OPENAI_API_KEY", FAKE_KEY)
    expected = original.replace(b"sk-test-old0000000000", FAKE_KEY.encode())
    assert env_path.read_bytes() == expected


def test_set_appends_new_name_and_creates_file(env_path):
    set_env_value(env_path, "AOC_ANTHROPIC_API_KEY", "sk-ant-test-0000000000abcd")
    assert read_env(env_path) == ["AOC_ANTHROPIC_API_KEY=sk-ant-test-0000000000abcd\n"]
    env_path.write_bytes(b"AOC_PORT=9000")
    set_env_value(env_path, "AOC_OPENAI_API_KEY", FAKE_KEY)
    assert env_path.read_bytes() == f"AOC_PORT=9000\nAOC_OPENAI_API_KEY={FAKE_KEY}\n".encode()


def test_value_with_hash_and_equals_quoted_roundtrip(env_path):
    value = "sk-test-a#b=c\"d\\e f'g"
    env_path.write_text("AOC_PORT=9000\n", encoding="utf-8")
    set_env_value(env_path, "AOC_OPENAI_API_KEY", value)
    line = read_env(env_path)[1]
    assert line.startswith('AOC_OPENAI_API_KEY="')
    loaded = Settings(_env_file=env_path)
    assert loaded.openai_api_key.get_secret_value() == value
    assert loaded.port == 9000


def test_remove_key(env_path):
    env_path.write_text(
        "# komentarz\nAOC_OPENAI_API_KEY=sk-test-1111111111\nAOC_PORT=9000\n", encoding="utf-8"
    )
    remove_env_value(env_path, "AOC_OPENAI_API_KEY")
    assert env_path.read_text(encoding="utf-8") == "# komentarz\nAOC_PORT=9000\n"
    remove_env_value(env_path, "AOC_OPENAI_API_KEY")
    assert env_path.read_text(encoding="utf-8") == "# komentarz\nAOC_PORT=9000\n"


def test_comment_with_same_name_untouched(env_path):
    env_path.write_bytes(b"# AOC_OPENAI_API_KEY=przyklad\n")
    set_env_value(env_path, "AOC_OPENAI_API_KEY", FAKE_KEY)
    assert read_env(env_path) == [
        "# AOC_OPENAI_API_KEY=przyklad\n",
        f"AOC_OPENAI_API_KEY={FAKE_KEY}\n",
    ]


def test_mask():
    assert mask(FAKE_KEY) == "••••abcd"
    assert mask("12345678") == "••••5678"
    assert mask("1234567") == "••••"
    assert mask("") == "••••"


def test_status_without_keys(client):
    body = client.get("/api/providers/status").json()
    assert {k["provider"] for k in body["keys"]} == {"openai", "anthropic", "azure_speech"}
    assert all(k["configured"] is False and k["masked"] is None for k in body["keys"])
    assert body["roles"]["transcription"] == {"provider": "openai", "model": "gpt-transcribe"}
    assert body["roles"]["generation"] == {"provider": "openai", "model": "gpt-6.1-sol"}
    assert body["roles"]["assessment"] == {"provider": "openai", "model": "gpt-6.1-sol"}


def test_status_endpoint_masked_never_plain(client):
    response = client.put("/api/providers/keys/openai", json={"value": f"  {FAKE_KEY}\n"})
    assert response.status_code == 200
    assert FAKE_KEY not in response.text
    status = client.get("/api/providers/status")
    assert FAKE_KEY not in status.text
    openai_entry = next(k for k in status.json()["keys"] if k["provider"] == "openai")
    assert openai_entry == {
        "provider": "openai",
        "configured": True,
        "masked": "••••abcd",
        "region": None,
    }


def test_put_reloads_running_app(app, client, env_path):
    with pytest.raises(ProviderError):
        get_text_generator(app.state.settings, "generation")
    client.put("/api/providers/keys/openai", json={"value": FAKE_KEY})
    assert f"AOC_OPENAI_API_KEY={FAKE_KEY}" in env_path.read_text(encoding="utf-8")
    generator = get_text_generator(app.state.settings, "generation")
    assert generator.provider == "openai"
    assert app.state.settings.env_file_path == env_path


def test_delete_removes_and_reloads(app, client, env_path):
    client.put("/api/providers/keys/openai", json={"value": FAKE_KEY})
    response = client.delete("/api/providers/keys/openai")
    assert response.status_code == 200
    entry = next(k for k in response.json()["keys"] if k["provider"] == "openai")
    assert entry["configured"] is False
    assert "AOC_OPENAI_API_KEY" not in env_path.read_text(encoding="utf-8")
    assert app.state.settings.openai_api_key is None


@pytest.mark.parametrize("value", ["", "   ", "sk-test abc", "sk-test\nabc", "sk-test\tabc"])
def test_put_rejects_whitespace_422(client, env_path, value):
    response = client.put("/api/providers/keys/openai", json={"value": value})
    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid key."
    assert not env_path.exists()


def test_azure_requires_region_422(client, env_path):
    response = client.put("/api/providers/keys/azure_speech", json={"value": "abcd1234efgh"})
    assert response.status_code == 422
    assert not env_path.exists()
    ok = client.put(
        "/api/providers/keys/azure_speech", json={"value": "abcd1234efgh", "region": "westeurope"}
    )
    assert ok.status_code == 200
    entry = next(k for k in ok.json()["keys"] if k["provider"] == "azure_speech")
    assert entry == {
        "provider": "azure_speech",
        "configured": True,
        "masked": "••••efgh",
        "region": "westeurope",
    }
    removed = client.delete("/api/providers/keys/azure_speech").json()
    entry = next(k for k in removed["keys"] if k["provider"] == "azure_speech")
    assert entry["configured"] is False and entry["region"] is None


def test_unknown_provider_422(client):
    assert client.put("/api/providers/keys/foo", json={"value": FAKE_KEY}).status_code == 422


def _mock_factory(monkeypatch, handler):
    monkeypatch.setattr(
        providers_router,
        "http_client_factory",
        lambda: httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def test_key_test_endpoint_auth_failure(client, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(401, json={"error": {"message": "Incorrect API key"}})

    _mock_factory(monkeypatch, handler)
    client.put("/api/providers/keys/openai", json={"value": FAKE_KEY})
    response = client.post("/api/providers/keys/openai/test")
    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "error_kind": "auth",
        "message": "The provider rejected the key.",
    }
    assert requests[0].url.path.endswith("/models")


def test_key_test_endpoint_ok_anthropic(client, monkeypatch):
    def handler(request):
        return httpx2.Response(
            200, json={"data": [], "has_more": False, "first_id": None, "last_id": None}
        )

    _mock_factory(monkeypatch, handler)
    client.put("/api/providers/keys/anthropic", json={"value": "sk-ant-test-0000000000abcd"})
    body = client.post("/api/providers/keys/anthropic/test").json()
    assert body["ok"] is True
    assert body["error_kind"] is None


def test_key_test_network_failure(client, monkeypatch):
    def handler(request):
        raise httpx2.ConnectError("down", request=request)

    _mock_factory(monkeypatch, handler)
    client.put("/api/providers/keys/openai", json={"value": FAKE_KEY})
    body = client.post("/api/providers/keys/openai/test").json()
    assert body["ok"] is False
    assert body["error_kind"] in {"network", "timeout"}


def test_key_test_without_key(client):
    body = client.post("/api/providers/keys/openai/test").json()
    assert body == {
        "ok": False,
        "error_kind": "unavailable",
        "message": "No API key for: openai. Add it in Settings.",
    }


def test_key_test_azure_without_key(client):
    response = client.post("/api/providers/keys/azure_speech/test")
    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "error_kind": "unavailable",
        "message": "No API key for: azure_speech. Add it in Settings.",
    }


@pytest.mark.parametrize("value", ["“sk-abcdefgh12345”", "sk-test\x00abcdef", "klucz-zażółć"])
def test_put_rejects_non_ascii_or_control_422(client, env_path, value):
    response = client.put("/api/providers/keys/openai", json={"value": value})
    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid key."
    assert not env_path.exists()
