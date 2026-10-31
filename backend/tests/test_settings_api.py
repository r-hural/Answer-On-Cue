import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from tests.conftest import local_client

WEIGHTS_MESSAGE = "Correctness and completeness weights must each be 10–70 and sum to 80."
BANDS_MESSAGE = "Band bounds must increase and lie within 1–99."


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "AOC_FACTUAL_WEIGHTS",
        "AOC_FACTUAL_BAND_BOUNDS",
        "AOC_VOICE_NAME",
        "AOC_DATA_DIR",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def app(tmp_path):
    settings = load_settings(
        data_dir=tmp_path / "data", frontend_dist=tmp_path / "dist", base_env_file=None
    )
    return create_app(settings)


@pytest.fixture
def client(app) -> TestClient:
    return local_client(app)


@pytest.fixture
def user_file(app):
    return app.state.settings.user_env_file


def test_get_preferences_defaults(client):
    body = client.get("/api/settings").json()
    assert body["factual_weights"] == [30, 50]
    assert body["factual_band_bounds"] == [30, 60, 80]
    assert body["voice_name"] == "pl_PL-bass-high"
    assert body["voice_provider"] == "piper"
    assert body["generation_provider"] == "openai"


def test_put_partial_writes_and_reloads(client, app, user_file):
    response = client.put("/api/settings", json={"factual_weights": [40, 40]})
    assert response.status_code == 200
    assert response.json()["factual_weights"] == [40, 40]
    assert app.state.settings.factual_weights == (40, 40)
    assert "AOC_FACTUAL_WEIGHTS=[40,40]" in user_file.read_text(encoding="utf-8")
    assert client.get("/api/settings").json()["factual_weights"] == [40, 40]


def test_put_invalid_weights_422_file_unchanged(client, app, user_file):
    client.put("/api/settings", json={"voice_rate": 1.2})
    before = user_file.read_bytes()
    response = client.put("/api/settings", json={"factual_weights": [40, 50]})
    assert response.status_code == 422
    assert response.json()["detail"] == WEIGHTS_MESSAGE
    response = client.put(
        "/api/settings", json={"voice_rate": 0.9, "factual_band_bounds": [60, 30, 80]}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == BANDS_MESSAGE
    assert user_file.read_bytes() == before
    assert app.state.settings.factual_weights == (30, 50)
    assert app.state.settings.voice_rate == 1.2


def test_put_unknown_field_422(client, user_file):
    response = client.put("/api/settings", json={"port": 1})
    assert response.status_code == 422
    assert not user_file.exists()


def test_bad_model_name_422(client, user_file):
    for name in ("", "model é", "x" * 101, "a\nb", "${AOC_OPENAI_API_KEY}", "a b"):
        response = client.put("/api/settings", json={"generation_model": name})
        assert response.status_code == 422
        assert response.json()["detail"] == "Invalid model name."
    assert not user_file.exists()


def test_put_keeps_other_lines(client, user_file):
    user_file.parent.mkdir(parents=True, exist_ok=True)
    user_file.write_bytes(b"# mine\r\nAOC_OPENAI_API_KEY=sk-test-0000000000abcd\r\n")
    response = client.put(
        "/api/settings", json={"generation_provider": "anthropic", "generation_model": "m-1"}
    )
    assert response.status_code == 200
    text = user_file.read_bytes().decode("utf-8")
    assert text.startswith("# mine\r\nAOC_OPENAI_API_KEY=sk-test-0000000000abcd\r\n")
    assert "AOC_GENERATION_PROVIDER=anthropic" in text
    assert client.get("/api/settings").json()["generation_model"] == "m-1"
