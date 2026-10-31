"""PUT /api/languages/{code} refuses a voice that cannot read the language."""

import pytest
from test_languages_api import by_code, db  # noqa: F401

from app.features.languages.models import ContentLanguage
from app.features.languages.service import (
    VOICE_NAME_WITHOUT_PROVIDER,
    VOICE_PROVIDER_UNKNOWN,
    VOICE_UNKNOWN,
)
from app.features.voice.openai_realtime import UNKNOWN_OPENAI_VOICE
from app.features.voice.per_language import VOICE_WRONG_LANGUAGE


def put(client, code: str, **body):
    return client.put(f"/api/languages/{code}", json=body)


def voice_of(client, code: str) -> tuple:
    stored = by_code(client, code)
    return stored["voice_provider"], stored["voice_name"]


@pytest.mark.parametrize(
    ("provider", "voice"),
    [
        ("piper", "de_DE-thorsten-high"),  # not installed: the download may come after
        ("azure_speech", "de-DE-KatjaNeural"),
        ("openai", "cedar"),
        ("browser", "browser"),
        ("piper", None),
    ],
)
def test_matching_voices_accepted(client, db, provider, voice):  # noqa: F811
    response = put(client, "de", voice_provider=provider, voice_name=voice)
    assert response.status_code == 200, response.text
    assert voice_of(client, "de") == (provider, voice)


@pytest.mark.parametrize(
    ("provider", "voice"), [("piper", "pl_PL-bass-high"), ("azure_speech", "pl-PL-ZofiaNeural")]
)
def test_wrong_language_refused(client, db, provider, voice):  # noqa: F811
    assert put(client, "de", voice_provider="piper", voice_name="de_DE-thorsten-high").is_success
    response = put(client, "de", voice_provider=provider, voice_name=voice)
    assert response.status_code == 422
    assert response.json()["detail"] == VOICE_WRONG_LANGUAGE
    assert voice_of(client, "de") == ("piper", "de_DE-thorsten-high")


def test_unknown_openai_and_browser_names(client, db):  # noqa: F811
    response = put(client, "de", voice_provider="openai", voice_name="nobody")
    assert response.status_code == 422
    assert response.json()["detail"] == UNKNOWN_OPENAI_VOICE == "Unknown OpenAI voice."
    response = put(client, "de", voice_provider="browser", voice_name="x")
    assert response.status_code == 422
    assert response.json()["detail"] == VOICE_UNKNOWN == "Unknown voice."
    assert voice_of(client, "de") == (None, None)


def test_voice_name_alone_checked_against_the_stored_provider(client, db):  # noqa: F811
    assert put(client, "de", voice_provider="piper").is_success
    response = put(client, "de", voice_name="pl_PL-gosia-medium")
    assert response.status_code == 422
    assert response.json()["detail"] == VOICE_WRONG_LANGUAGE
    assert voice_of(client, "de") == ("piper", None)
    assert put(client, "de", voice_name="de_DE-thorsten-medium").is_success


def test_polish_takes_polish_voices(client, db):  # noqa: F811
    assert put(client, "pl", voice_provider="piper", voice_name="pl_PL-gosia-medium").is_success
    response = put(client, "pl", voice_provider="azure_speech", voice_name="de-DE-KatjaNeural")
    assert response.status_code == 422


def test_t30_rules_first(client, db):  # noqa: F811
    response = put(client, "de", voice_provider="espeak")
    assert response.status_code == 422
    assert response.json()["detail"] == VOICE_PROVIDER_UNKNOWN
    response = put(client, "de", voice_name="x")
    assert response.status_code == 422
    assert response.json()["detail"] == VOICE_NAME_WITHOUT_PROVIDER


def test_a_patch_without_voice_fields_keeps_an_old_voice(client, db):  # noqa: F811
    # A row stored before T33 may hold a voice of another language; enabling it still works.
    row = db.get(ContentLanguage, "de")
    row.voice_provider, row.voice_name = "piper", "pl_PL-bass-high"
    db.commit()
    response = put(client, "de", enabled=True)
    assert response.status_code == 200, response.text
    assert voice_of(client, "de") == ("piper", "pl_PL-bass-high")
    response = put(client, "de", voice_name="pl_PL-gosia-medium")
    assert response.status_code == 422
