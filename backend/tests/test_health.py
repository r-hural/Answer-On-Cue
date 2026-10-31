from app.features.system.version import app_version


def test_health_returns_ok(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": "answeroncue", "version": app_version()}


def test_health_version_equals_version_file(client):
    from app.config import REPO_ROOT

    expected = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert client.get("/api/health").json()["version"] == expected
