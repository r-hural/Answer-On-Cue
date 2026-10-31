from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from test_backup_support import (
    NOW,
    add_answered_round,
    add_recording,
    statements_of,
    subject_with_history,
    writes_in,
)
from test_reports_history import count_statements
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.backup import storage
from app.features.recording.models import Recording
from app.main import create_app


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(storage, "utcnow", lambda: NOW)


def _present(db, settings) -> set[int]:
    db.expire_all()
    return {
        r.id
        for r in db.query(Recording).all()
        if r.audio_deleted_at is None and (settings.data_dir / r.stored_path).is_file()
    }


def test_storage_sizes(client, db, settings):
    history = subject_with_history(db, settings)
    body = client.get("/api/data/storage").json()
    wavs = [settings.data_dir / r.stored_path for r in (*history["recordings"], history["mic"])]
    db_file = settings.data_dir / "answeroncue.db"
    assert body["data_dir"] == str(settings.data_dir)
    assert body["database_bytes"] == db_file.stat().st_size
    assert body["documents_bytes"] == len(b"Ateny i Sparta.")
    assert body["recordings_bytes"] == sum(w.stat().st_size for w in wavs)
    assert body["total_bytes"] >= body["database_bytes"] + body["recordings_bytes"]
    assert (body["recordings"], body["recordings_deleted"]) == (3, 0)
    assert body["audio_retention"] == "forever"


def test_retention_preview_then_put(client, db, settings):
    young = add_recording(db, settings, age=timedelta(days=3))
    middle = add_recording(db, settings, age=timedelta(days=40))
    old = add_recording(db, settings, age=timedelta(days=100))
    preview = client.get("/api/data/retention/preview", params={"policy": "30d"}).json()
    assert preview["recordings"] == 2
    assert preview["bytes"] == middle.size_bytes + old.size_bytes
    assert preview["kept_unresolved"] == 0 and len(preview["fingerprint"]) == 16

    before = settings.env_file_path.read_text() if settings.env_file_path.exists() else ""
    rejected = client.put("/api/data/retention", json={"policy": "7d"})
    assert rejected.status_code == 422
    after = settings.env_file_path.read_text() if settings.env_file_path.exists() else ""
    assert after == before

    done = client.put(
        "/api/data/retention", json={"policy": "30d", "fingerprint": preview["fingerprint"]}
    )
    assert done.status_code == 200, done.text
    assert done.json() == {
        "deleted": 2,
        "bytes": middle.size_bytes + old.size_bytes,
        "skipped_in_use": 0,
    }
    assert "AOC_AUDIO_RETENTION=30d" in settings.env_file_path.read_text()
    assert _present(db, settings) == {young.id}
    body = client.get("/api/data/storage").json()
    assert body["audio_retention"] == "30d" and body["recordings_deleted"] == 2


def test_retention_put_with_a_stale_fingerprint_changes_nothing(client, db, settings):
    add_recording(db, settings, age=timedelta(days=40))
    preview = client.get("/api/data/retention/preview", params={"policy": "30d"}).json()
    newer = add_recording(db, settings, age=timedelta(days=50))
    stale = client.put(
        "/api/data/retention", json={"policy": "30d", "fingerprint": preview["fingerprint"]}
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["preview"]["recordings"] == 2
    assert newer.id in _present(db, settings)
    assert not settings.env_file_path.exists() or "RETENTION" not in (
        settings.env_file_path.read_text()
    )


def _delete_now(client, scope, **params):
    query = {"scope": scope, **params}
    preview = client.get("/api/data/recordings/deletion-preview", params=query).json()
    body = {"scope": scope, "fingerprint": preview["fingerprint"], **params}
    return preview, client.post("/api/data/recordings/delete", json=body)


def test_delete_now_scopes(client, db, settings):
    history = subject_with_history(db, settings)
    old = add_recording(db, settings, age=timedelta(days=40))
    older = add_recording(db, settings, age=timedelta(days=100))
    everything = _present(db, settings)

    preview, done = _delete_now(client, "older_than", days=30)
    assert preview["recordings"] == 2 and done.status_code == 200, done.text
    assert done.json()["deleted"] == 2
    assert _present(db, settings) == everything - {old.id, older.id}

    mine = {r.id for r in history["recordings"]}
    preview, done = _delete_now(client, "subject", subject_id=history["subject"].id)
    assert preview["recordings"] == 2 and done.json()["deleted"] == 2
    assert _present(db, settings) == {history["mic"].id}

    preview, done = _delete_now(client, "all")
    assert preview["recordings"] == 1 and done.json()["deleted"] == 1
    assert not _present(db, settings) and mine


def test_delete_now_with_a_stale_fingerprint(client, db, settings):
    old = add_recording(db, settings, age=timedelta(days=40))
    preview = client.get("/api/data/recordings/deletion-preview", params={"scope": "all"}).json()
    newer = add_recording(db, settings, age=timedelta(days=50))
    stale = client.post(
        "/api/data/recordings/delete",
        json={"scope": "all", "fingerprint": preview["fingerprint"]},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["preview"]["recordings"] == 2
    assert _present(db, settings) == {old.id, newer.id}


def test_delete_now_validates_its_scope(client, db, settings):
    assert (
        client.get(
            "/api/data/recordings/deletion-preview", params={"scope": "older_than"}
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/data/recordings/deletion-preview", params={"scope": "subject", "subject_id": 999}
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/data/recordings/deletion-preview", params={"scope": "nonsense"}
        ).status_code
        == 422
    )


def test_storage_needs_a_local_host(settings, db):
    foreign = TestClient(create_app(settings), base_url="http://evil.example")
    assert foreign.get("/api/data/storage").status_code == 403


def test_guarded(settings, db):
    bare = TestClient(create_app(settings))
    assert bare.put("/api/data/retention", json={"policy": "30d"}).status_code == 403
    assert (
        bare.post(
            "/api/data/recordings/delete", json={"scope": "all", "fingerprint": "x"}
        ).status_code
        == 403
    )


def test_read_endpoints_budget(client, db, settings):
    history = subject_with_history(db, settings)
    calls = {
        "storage": lambda: client.get("/api/data/storage"),
        "retention": lambda: client.get(
            "/api/data/retention/preview", params={"policy": "assessed"}
        ),
        "recordings": lambda: client.get(
            "/api/data/recordings/deletion-preview", params={"scope": "all"}
        ),
    }
    small = {name: count_statements(client, call) for name, call in calls.items()}
    for days_ago in range(2, 5):
        add_answered_round(db, settings, history, days_ago=days_ago)
    for _ in range(6):
        add_recording(db, settings, age=timedelta(days=3))
    for name, call in calls.items():
        assert count_statements(client, call) == small[name], name
        assert writes_in(statements_of(call)) == [], name
