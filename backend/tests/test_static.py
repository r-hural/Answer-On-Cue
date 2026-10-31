import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def dist_client(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<h1>AnswerOnCue</h1>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.ico").write_bytes(b"ico")
    settings = Settings(data_dir=tmp_path / "data", frontend_dist=dist)
    return TestClient(create_app(settings))


def test_root_serves_index(dist_client):
    response = dist_client.get("/")
    assert response.status_code == 200
    assert "AnswerOnCue" in response.text


def test_spa_fallback(dist_client):
    response = dist_client.get("/library/123")
    assert response.status_code == 200
    assert "AnswerOnCue" in response.text


def test_assets_served(dist_client):
    response = dist_client.get("/assets/app.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


def test_existing_file_served_directly(dist_client):
    response = dist_client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.content == b"ico"


@pytest.mark.parametrize(
    "path", ["..%5Csecret", "%5C%5Chost%5Cshare%5Cx", "C:/Windows/win.ini", "a/../../x"]
)
def test_suspicious_paths_fall_back_to_index(dist_client, path):
    response = dist_client.get(f"/{path}")
    assert response.status_code == 200
    assert "AnswerOnCue" in response.text


def test_api_404_stays_json(dist_client):
    response = dist_client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_missing_dist_hint(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert "scripts/dev.ps1 -Build" in response.text
    assert client.get("/api/health").status_code == 200
