import shutil
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from test_backup_archive import apply, backup_path, make_backup, read_members, upload, variant
from test_backup_support import subject_with_history
from test_topics_support import BACKEND, upgrade

from app.db.session import SessionLocal
from app.features.backup import archive
from app.features.backup.export import folder_name
from app.features.languages.profiles import SUPPORTED
from app.main import create_app

PL_VOICE = {"voice_provider": "piper", "voice_name": "pl_PL-darkman-medium"}


@pytest.fixture
def secret_settings(settings):
    """The backup tests' settings name; no keys are needed here."""
    return settings


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def api(settings):
    from conftest import local_client

    return local_client(create_app(settings))


@pytest.fixture
def history(db, settings) -> dict:
    return subject_with_history(db, settings)


@pytest.fixture
def good(api, db, settings, history, tmp_path) -> Path:
    kept = tmp_path / "good.zip"
    shutil.copyfile(backup_path(settings, make_backup(api)), kept)
    return kept


def language_rows(settings) -> dict[str, tuple]:
    engine = create_engine(settings.effective_database_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT code, enabled, voice_provider, voice_name FROM content_language")
        ).all()
    engine.dispose()
    return {code: (bool(enabled), provider, name) for code, enabled, provider, name in rows}


def subject_languages(settings) -> dict[str, str]:
    engine = create_engine(settings.effective_database_url)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT name, language FROM subject")).all()
    engine.dispose()
    return dict(rows)


def restore(api, path: Path):
    preview = upload(api, path)
    assert preview.status_code == 200, preview.text
    result = apply(api, preview.json()["token"])
    assert result.status_code == 200, result.text
    return result


def put(api, code: str, body: dict) -> None:
    response = api.put(f"/api/languages/{code}", json=body)
    assert response.status_code == 200, response.text


def edited_db(good: Path, tmp_path: Path, *statements: str) -> bytes:
    path = tmp_path / "edited.db"
    path.write_bytes(read_members(good)["answeroncue.db"])
    conn = sqlite3.connect(path)
    for statement in statements:
        conn.execute(statement)
    conn.commit()
    conn.close()
    return path.read_bytes()


def older_backup(good: Path, tmp_path: Path) -> Path:
    old_db = tmp_path / "old.db"
    old_db.write_bytes(read_members(good)["answeroncue.db"])
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url=sqlite:///{old_db.as_posix()}"]})()
    command.downgrade(cfg, "0015_doc_versions")
    return variant(
        good,
        tmp_path / "older.zip",
        replace={"answeroncue.db": old_db.read_bytes()},
        patch={"alembic_revision": "0015_doc_versions"},
        rehash=True,
    )


def test_backup_round_trip_keeps_languages(api, db, secret_settings, history, tmp_path):
    put(api, "de", {"enabled": True})
    created = api.post("/api/subjects", json={"name": "Geschichte", "language": "de"})
    assert created.status_code == 201, created.text
    put(api, "pl", PL_VOICE)
    backup = backup_path(secret_settings, make_backup(api))

    deleted = api.delete(f"/api/subjects/{created.json()['id']}?confirm=true")
    assert deleted.status_code == 200, deleted.text
    put(api, "de", {"enabled": False})
    put(api, "pl", {"voice_provider": None})
    assert language_rows(secret_settings)["pl"] == (True, None, None)

    restore(api, backup)
    assert subject_languages(secret_settings)["Geschichte"] == "de"
    rows = language_rows(secret_settings)
    assert rows["de"][0] is True
    assert rows["pl"] == (True, "piper", "pl_PL-darkman-medium")


def test_old_backup_restores_polish_and_keeps_live_languages(
    api, db, secret_settings, history, good, tmp_path
):
    older = older_backup(good, tmp_path)
    put(api, "en", {"enabled": True})
    put(api, "pl", PL_VOICE)
    before = language_rows(secret_settings)
    restore(api, older)
    assert set(subject_languages(secret_settings).values()) == {"pl"}
    after = language_rows(secret_settings)
    assert after == before
    assert after["en"][0] is True
    assert after["pl"] == (True, "piper", "pl_PL-darkman-medium")
    assert after["de"][0] is False


def test_old_backup_enables_polish_when_live_disabled(
    api, db, secret_settings, history, good, tmp_path
):
    older = older_backup(good, tmp_path)
    engine = create_engine(secret_settings.effective_database_url)
    with engine.begin() as conn:
        conn.execute(text("UPDATE content_language SET enabled = (code = 'en')"))
    engine.dispose()
    assert language_rows(secret_settings)["pl"][0] is False
    restore(api, older)
    rows = language_rows(secret_settings)
    assert rows["pl"][0] is True
    assert rows["en"][0] is True


def test_restore_enables_used_languages(api, db, secret_settings, history, good, tmp_path):
    data = edited_db(
        good,
        tmp_path,
        "UPDATE subject SET language = 'fr'",
        "UPDATE content_language SET enabled = 0 WHERE code = 'fr'",
    )
    french = variant(good, tmp_path / "fr.zip", replace={"answeroncue.db": data}, rehash=True)
    restore(api, french)
    assert set(subject_languages(secret_settings).values()) == {"fr"}
    assert language_rows(secret_settings)["fr"][0] is True


def test_restore_adds_missing_language_rows(api, db, secret_settings, history, good, tmp_path):
    """A backup whose table lacks a supported language still lists it, enabled when used."""
    data = edited_db(
        good,
        tmp_path,
        "UPDATE subject SET language = 'fr'",
        "DELETE FROM content_language WHERE code IN ('fr', 'uk')",
    )
    partial = variant(good, tmp_path / "partial.zip", replace={"answeroncue.db": data}, rehash=True)
    restore(api, partial)
    listed = api.get("/api/languages").json()
    assert [language["code"] for language in listed] == list(SUPPORTED)
    rows = language_rows(secret_settings)
    assert rows["fr"] == (True, None, None)
    assert rows["uk"] == (False, None, None)
    put(api, "uk", {"enabled": True})


def test_restore_refuses_unknown_language(api, db, secret_settings, history, good, tmp_path):
    data = edited_db(good, tmp_path, "UPDATE subject SET language = 'xx'")
    unknown = variant(good, tmp_path / "xx.zip", replace={"answeroncue.db": data}, rehash=True)
    before = subject_languages(secret_settings)
    response = upload(api, unknown)
    assert response.status_code == 422
    assert response.json()["detail"] == archive.DAMAGED
    assert subject_languages(secret_settings) == before


def test_export_folder_names_keep_letters():
    assert folder_name("Історія України", 1, set()) == "Історія України"
    assert folder_name("Économie générale", 2, set()) == "Économie générale"
    assert folder_name("Straße: Teil 1", 3, set()) == "Straße_ Teil 1"
