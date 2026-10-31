import pytest
from alembic import command
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from test_backup_support import statements_of, writes_in
from test_reports_history import count_statements
from test_topics_support import BACKEND, make_subject, upgrade

from app.db.session import SessionLocal
from app.features.languages.models import ContentLanguage
from app.features.languages.profiles import PROFILES, SUPPORTED
from app.features.languages.service import (
    LANGUAGE_IN_USE,
    LANGUAGE_UNKNOWN,
    LAST_LANGUAGE,
    VOICE_NAME_WITHOUT_PROVIDER,
    VOICE_PROVIDER_UNKNOWN,
    enabled_codes,
    get_language_row,
    profile_for_subject,
    subject_language,
)
from app.main import create_app

BEFORE = "0015_doc_versions"


def alembic_config(url: str):
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()
    return cfg


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def set_enabled(db, code: str, enabled: bool = True) -> None:
    db.get(ContentLanguage, code).enabled = enabled
    db.commit()


def language_rows(url: str) -> list[tuple]:
    with create_engine(url).connect() as conn:
        return conn.execute(
            text(
                "SELECT code, enabled, voice_provider, voice_name FROM content_language"
                " ORDER BY rowid"
            )
        ).all()


def test_migration_adds_language_and_table(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/m.db"
    cfg = alembic_config(url)
    command.upgrade(cfg, BEFORE)
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO subject (name) VALUES ('Historia')"))
    command.upgrade(cfg, "head")

    inspector = inspect(create_engine(url))
    column = {c["name"]: c for c in inspector.get_columns("subject")}["language"]
    assert column["nullable"] is False
    assert "pl" in str(column["default"])
    assert inspector.get_foreign_keys("subject") == []
    assert [tuple(r) for r in language_rows(url)] == [
        (code, 1 if code == "pl" else 0, None, None) for code in SUPPORTED
    ]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT name, language FROM subject")).all() == [
            ("Historia", "pl")
        ]

    command.downgrade(cfg, BEFORE)
    inspector = inspect(create_engine(url))
    assert "content_language" not in inspector.get_table_names()
    assert "language" not in {c["name"] for c in inspector.get_columns("subject")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT name FROM subject")).all() == [("Historia",)]
    command.upgrade(cfg, "head")
    assert len(language_rows(url)) == 7


def test_get_languages(client, db):
    response = client.get("/api/languages")
    assert response.status_code == 200
    items = response.json()
    assert [i["code"] for i in items] == list(SUPPORTED)
    assert items[0] == {
        "code": "pl",
        "bcp47": "pl-PL",
        "name_en": "Polish",
        "name_native": "polski",
        "quotes": ["„", "”"],
        "enabled": True,
        "voice_provider": None,
        "voice_name": None,
        "subject_count": 0,
    }
    make_subject(db, "Historia")
    make_subject(db, "Biologia")
    db.commit()
    counts = {i["code"]: i["subject_count"] for i in client.get("/api/languages").json()}
    assert counts == {code: 2 if code == "pl" else 0 for code in SUPPORTED}


def test_get_languages_statements_constant(client, db):
    make_subject(db, "Historia")
    db.commit()
    small = count_statements(client, lambda: client.get("/api/languages"))
    set_enabled(db, "de")
    set_enabled(db, "en")
    for n, code in enumerate(["pl", "de", "en", "pl", "de", "en", "pl", "de"]):
        subject = make_subject(db, f"Przedmiot {n}")
        subject.language = code
    db.commit()
    large = count_statements(client, lambda: client.get("/api/languages"))
    assert small == large
    assert large <= 2
    assert writes_in(statements_of(lambda: client.get("/api/languages"))) == []
    counts = {i["code"]: i["subject_count"] for i in client.get("/api/languages").json()}
    assert (counts["pl"], counts["de"], counts["en"]) == (4, 3, 2)


def by_code(client, code: str) -> dict:
    return next(i for i in client.get("/api/languages").json() if i["code"] == code)


def test_put_enables_and_disables(client, db):
    response = client.put("/api/languages/de", json={"enabled": True})
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is True
    assert by_code(client, "de")["enabled"] is True
    response = client.put("/api/languages/de", json={"enabled": False})
    assert response.status_code == 200
    assert by_code(client, "de")["enabled"] is False


def test_put_is_guarded(settings, db, client):
    plain = TestClient(create_app(settings))
    assert plain.put("/api/languages/de", json={"enabled": True}).status_code == 403
    assert by_code(client, "de")["enabled"] is False


def test_put_unknown_code(client, db):
    response = client.put("/api/languages/xx", json={"enabled": True})
    assert response.status_code == 404
    assert response.json()["detail"] == LANGUAGE_UNKNOWN


def test_put_enabled_null_refused(client, db):
    """Null clears a voice field, but "enabled" has nothing to clear."""
    response = client.put("/api/languages/pl", json={"enabled": None})
    assert response.status_code == 422
    assert by_code(client, "pl")["enabled"] is True
    response = client.put("/api/languages/de", json={"enabled": None, "voice_provider": "piper"})
    assert response.status_code == 422
    assert by_code(client, "de")["voice_provider"] is None


def test_disable_used_language_refused(client, db):
    set_enabled(db, "en")
    make_subject(db, "Historia")
    db.commit()
    response = client.put("/api/languages/pl", json={"enabled": False})
    assert response.status_code == 409
    assert response.json()["detail"] == LANGUAGE_IN_USE
    assert by_code(client, "pl")["enabled"] is True


def test_last_enabled_language_refused(client, db):
    response = client.put("/api/languages/pl", json={"enabled": False})
    assert response.status_code == 409
    assert response.json()["detail"] == LAST_LANGUAGE
    assert by_code(client, "pl")["enabled"] is True
    assert client.put("/api/languages/en", json={"enabled": True}).status_code == 200
    response = client.put("/api/languages/pl", json={"enabled": False})
    assert response.status_code == 200
    assert response.json()["enabled"] is False


def test_voice_fields(client, db):
    response = client.put(
        "/api/languages/de",
        json={"voice_provider": "piper", "voice_name": "de_DE-thorsten-high"},
    )
    assert response.status_code == 200, response.text
    assert (response.json()["voice_provider"], response.json()["voice_name"]) == (
        "piper",
        "de_DE-thorsten-high",
    )
    response = client.put("/api/languages/de", json={"voice_provider": "espeak"})
    assert response.status_code == 422
    assert response.json()["detail"] == VOICE_PROVIDER_UNKNOWN
    assert by_code(client, "de")["voice_provider"] == "piper"

    response = client.put("/api/languages/de", json={"voice_provider": None})
    assert response.status_code == 200
    assert (response.json()["voice_provider"], response.json()["voice_name"]) == (None, None)

    response = client.put("/api/languages/de", json={"voice_name": "x"})
    assert response.status_code == 422
    assert response.json()["detail"] == VOICE_NAME_WITHOUT_PROVIDER

    response = client.put(
        "/api/languages/de",
        json={"voice_provider": "azure_speech", "voice_name": "de-DE-KatjaNeural"},
    )
    assert response.status_code == 200
    stored = by_code(client, "de")
    assert (stored["voice_provider"], stored["voice_name"]) == (
        "azure_speech",
        "de-DE-KatjaNeural",
    )
    assert stored["enabled"] is False  # untouched fields stay

    response = client.put("/api/languages/de", json={"enabled": True, "extra": 1})
    assert response.status_code == 422
    assert by_code(client, "de")["enabled"] is False


def test_service_helpers(db):
    assert enabled_codes(db) == ["pl"]
    assert get_language_row(db, "fr").enabled is False
    with pytest.raises(HTTPException) as missing:
        get_language_row(db, "xx")
    assert missing.value.status_code == 404
    subject = make_subject(db, "Historia")
    db.commit()
    assert subject_language(db, subject.id) == "pl"
    with pytest.raises(HTTPException) as gone:
        subject_language(db, subject.id + 100)
    assert gone.value.status_code == 404
    assert profile_for_subject(db, subject.id) is PROFILES["pl"]
