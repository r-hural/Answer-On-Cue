import pytest
from fastapi.testclient import TestClient

from app.config import Settings, load_settings
from app.main import create_app
from tests.conftest import local_client

KEY = "sk-test-0000000000abcd"
ENV_NAMES = (
    "AOC_DATA_DIR",
    "AOC_TRANSCRIPTION_MODEL",
    "AOC_OPENAI_API_KEY",
    "AOC_ANTHROPIC_API_KEY",
    "AOC_AZURE_SPEECH_KEY",
    "AOC_AZURE_SPEECH_REGION",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_user_file_defaults_to_data_dir(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert settings.user_env_file == tmp_path / "settings.env"


def test_load_settings_precedence(tmp_path, monkeypatch):
    base = tmp_path / "base.env"
    base.write_text("AOC_TRANSCRIPTION_MODEL=model-a\n", encoding="utf-8")
    (tmp_path / "settings.env").write_text("AOC_TRANSCRIPTION_MODEL=model-b\n", encoding="utf-8")
    kwargs = {"data_dir": tmp_path, "base_env_file": base}
    monkeypatch.setenv("AOC_TRANSCRIPTION_MODEL", "model-c")
    assert load_settings(**kwargs).transcription_model == "model-c"
    monkeypatch.delenv("AOC_TRANSCRIPTION_MODEL")
    assert load_settings(**kwargs).transcription_model == "model-b"
    (tmp_path / "settings.env").unlink()
    assert load_settings(**kwargs).transcription_model == "model-a"
    base.unlink()
    assert (
        load_settings(**kwargs).transcription_model == Settings(_env_file=None).transcription_model
    )


def test_data_dir_in_user_file_ignored(tmp_path):
    (tmp_path / "settings.env").write_text(
        f"AOC_DATA_DIR={tmp_path / 'elsewhere'}\n", encoding="utf-8"
    )
    settings = load_settings(data_dir=tmp_path, base_env_file=None)
    assert settings.data_dir == tmp_path


@pytest.fixture
def base_file(tmp_path):
    path = tmp_path / "base.env"
    path.write_text("AOC_PORT=9000\n", encoding="utf-8")
    return path


@pytest.fixture
def app_settings(tmp_path, base_file) -> Settings:
    return load_settings(
        data_dir=tmp_path / "data", frontend_dist=tmp_path / "dist", base_env_file=base_file
    )


@pytest.fixture
def api(app_settings) -> TestClient:
    return local_client(create_app(app_settings))


def test_put_key_writes_user_file_in_data_dir(api, app_settings, base_file):
    before = base_file.read_bytes()
    response = api.put("/api/providers/keys/openai", json={"value": KEY})
    assert response.status_code == 200
    user_file = app_settings.data_dir / "settings.env"
    assert f"AOC_OPENAI_API_KEY={KEY}" in user_file.read_text(encoding="utf-8")
    assert base_file.read_bytes() == before


def test_delete_key_removes_from_both_files(api, app_settings, base_file):
    base_file.write_text(f"AOC_PORT=9000\nAOC_OPENAI_API_KEY={KEY}\n", encoding="utf-8")
    api.put("/api/providers/keys/openai", json={"value": KEY + "x"})
    status = api.get("/api/providers/status").json()
    assert next(k for k in status["keys"] if k["provider"] == "openai")["configured"]
    response = api.delete("/api/providers/keys/openai")
    assert response.status_code == 200
    assert not next(k for k in response.json()["keys"] if k["provider"] == "openai")["configured"]
    assert "AOC_OPENAI_API_KEY" not in base_file.read_text(encoding="utf-8")
    user_text = (app_settings.data_dir / "settings.env").read_text(encoding="utf-8")
    assert "AOC_OPENAI_API_KEY" not in user_text
    assert "AOC_PORT=9000" in base_file.read_text(encoding="utf-8")


def test_no_key_in_any_response(api):
    bodies = [api.get("/api/providers/status").text]
    bodies.append(api.put("/api/providers/keys/openai", json={"value": KEY}).text)
    bodies.append(api.get("/api/providers/status").text)
    bad = api.put("/api/providers/keys/anthropic", json={"value": KEY + " bad", "extra": [KEY]})
    assert bad.status_code == 422
    bodies.append(bad.text)
    bodies.append(api.get("/api/settings").text)
    bodies.append(api.put("/api/settings", json={"transcription_model": KEY + "é"}).text)
    bodies.append(api.post("/api/providers/keys/azure_speech/test").text)
    bodies.append(api.delete("/api/providers/keys/openai").text)
    assert all(KEY not in body for body in bodies)
