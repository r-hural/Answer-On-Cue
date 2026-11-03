"""Settings and key changes accept only requests from the app's own page (CSRF, DNS rebinding)."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

LOCAL = {"X-AnswerOnCue": "1", "Host": "127.0.0.1:8770"}

CHANGES = [
    ("PUT", "/api/settings", {"voice_rate": 1.1}),
    ("PUT", "/api/providers/keys/openai", {"value": "sk-test-guard-0000"}),
    ("DELETE", "/api/providers/keys/openai", None),
]
CHANGE_IDS = ["settings", "put-key", "del-key"]


@pytest.fixture
def raw(settings) -> TestClient:
    return TestClient(create_app(settings))


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "127.0.0.1:8770"},
        {"X-Proba-Glosu": "1", "Host": "127.0.0.1:8770"},
        {"X-AnswerOnCue": "1", "Host": "evil.example:8770"},
        {**LOCAL, "Origin": "http://evil.example"},
    ],
    ids=["no-header", "old-header", "foreign-host", "foreign-origin"],
)
@pytest.mark.parametrize(("method", "path", "body"), CHANGES, ids=CHANGE_IDS)
def test_change_refused_unless_local(raw, settings, headers, method, path, body):
    response = raw.request(method, path, json=body, headers=headers)

    assert response.status_code == 403
    assert not settings.user_env_file.exists()


@pytest.mark.parametrize(("method", "path", "body"), CHANGES, ids=CHANGE_IDS)
def test_change_accepted_from_local_page(raw, method, path, body):
    response = raw.request(method, path, json=body, headers=LOCAL)

    assert response.status_code == 200


def test_reads_need_no_header(raw):
    assert raw.get("/api/settings").status_code == 200
    assert raw.get("/api/providers/status").status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {
            "X-AnswerOnCue": "1",
            "Host": "answer-on-cue.localhost:8770",
            "Origin": "http://answer-on-cue.localhost:8770",
        },
        {"X-AnswerOnCue": "1", "Host": "Answer-on-Cue.localhost:8770"},
    ],
    ids=["app-host", "app-host-mixed-case"],
)
def test_change_accepted_from_app_host(raw, headers):
    assert (
        raw.request("PUT", "/api/settings", json={"voice_rate": 1.1}, headers=headers).status_code
        == 200
    )


def test_other_localhost_name_refused(raw):
    headers = {"X-AnswerOnCue": "1", "Host": "evil.localhost:8770"}
    assert (
        raw.request("PUT", "/api/settings", json={"voice_rate": 1.1}, headers=headers).status_code
        == 403
    )
