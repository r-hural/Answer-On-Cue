import pytest
from fastapi.testclient import TestClient

from app.config import Settings, load_settings
from app.main import create_app
from tests.conftest import local_client


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "AOC_FACTUAL_WEIGHTS",
        "AOC_FACTUAL_BAND_BOUNDS",
        "AOC_VOICE_NAME",
        "AOC_DATA_DIR",
        "AOC_ROUND_QUESTION_COUNT",
        "AOC_ROUND_PREP_SECONDS",
        "AOC_ROUND_ANSWER_SECONDS",
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


def test_round_defaults_in_preferences(client):
    body = client.get("/api/settings").json()
    assert body["round_question_count"] == 10
    assert body["round_prep_seconds"] == 30
    assert body["round_answer_seconds"] == 120


def test_put_round_defaults_writes_and_reloads(client, user_file):
    response = client.put(
        "/api/settings", json={"round_prep_seconds": 0, "round_answer_seconds": 90}
    )
    assert response.status_code == 200
    text = user_file.read_text(encoding="utf-8")
    assert "AOC_ROUND_PREP_SECONDS=0" in text
    assert "AOC_ROUND_ANSWER_SECONDS=90" in text
    body = client.get("/api/settings").json()
    assert body["round_prep_seconds"] == 0
    assert body["round_answer_seconds"] == 90
    assert body["round_question_count"] == 10


@pytest.mark.parametrize(
    "field, value",
    [
        ("round_question_count", 0),
        ("round_question_count", 31),
        ("round_prep_seconds", -1),
        ("round_prep_seconds", 601),
        ("round_answer_seconds", 4),
        ("round_answer_seconds", 601),
    ],
)
def test_put_round_defaults_out_of_range(client, user_file, field, value):
    client.put("/api/settings", json={"voice_rate": 1.2})
    before = user_file.read_bytes()
    response = client.put("/api/settings", json={field: value})
    assert response.status_code == 422
    assert user_file.read_bytes() == before


def test_environment_sets_round_question_count(monkeypatch):
    monkeypatch.setenv("AOC_ROUND_QUESTION_COUNT", "12")
    settings = Settings(_env_file=None, base_env_file=None)
    assert settings.round_question_count == 12
