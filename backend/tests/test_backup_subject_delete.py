import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_backup_support import (
    NOW,
    add_answered_round,
    add_recording,
    statements_of,
    subject_with_history,
    writes_in,
)
from test_coverage_support import make_round
from test_reports_history import count_statements
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.library.models import Document, Subject
from app.features.recording.models import Recording
from app.features.rounds.models import Round
from app.main import create_app

BUSY = "Pause or end the running round of this subject first."


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def preview_of(client, subject_id: int) -> dict:
    response = client.get(f"/api/subjects/{subject_id}/deletion-preview")
    assert response.status_code == 200, response.text
    return response.json()


def test_preview_counts_everything(client, db, settings):
    history = subject_with_history(db, settings, "Historia")
    other = subject_with_history(db, settings, "Geografia")
    preview = preview_of(client, history["subject"].id)
    sizes = [(settings.data_dir / r.stored_path).stat().st_size for r in history["recordings"]]
    assert preview["subject"] == "Historia"
    assert (preview["documents"], preview["versions"], preview["passages"]) == (1, 1, 4)
    assert (preview["files"], preview["bytes"]) == (1, len(b"Ateny i Sparta."))
    assert (preview["topics"], preview["objectives"]) == (1, 2)
    assert (preview["questions"], preview["question_versions"]) == (2, 2)
    assert (preview["rounds"], preview["answers"]) == (1, 2)
    assert (preview["recordings"], preview["recording_bytes"]) == (2, sum(sizes))
    assert len(preview["fingerprint"]) == 16
    assert preview_of(client, other["subject"].id)["fingerprint"] != preview["fingerprint"]


def test_preview_counts_only_recordings_with_audio(client, db, settings):
    history = subject_with_history(db, settings)
    gone = history["recordings"][0]
    (settings.data_dir / gone.stored_path).unlink()
    gone.audio_deleted_at = NOW
    db.commit()
    preview = preview_of(client, history["subject"].id)
    assert preview["recordings"] == 1
    assert preview["recording_bytes"] == history["recordings"][1].size_bytes


def test_delete_removes_exactly_the_preview(client, db, settings):
    history = subject_with_history(db, settings, "Historia")
    other = subject_with_history(db, settings, "Geografia")
    db.execute(
        text(
            "INSERT INTO usage_event (operation_id, operation, task, role, provider, model,"
            " status, started_at, duration_ms, subject_id)"
            " VALUES ('op', 'attempt.assess', 'assessment', 'assessment', 'openai', 'fake',"
            " 'ok', '2026-10-24 00:00:00', 5, :s)"
        ),
        {"s": history["subject"].id},
    )
    db.commit()
    preview = preview_of(client, history["subject"].id)
    other_before = preview_of(client, other["subject"].id)
    mine_paths = [r.stored_path for r in history["recordings"]]
    kept = {*(r.id for r in other["recordings"]), other["mic"].id, history["mic"].id}
    kept_paths = [r.stored_path for r in (*other["recordings"], other["mic"], history["mic"])]
    history_id, other_id = history["subject"].id, other["subject"].id

    response = client.delete(
        f"/api/subjects/{history_id}",
        params={"confirm": "true", "fingerprint": preview["fingerprint"]},
    )
    assert response.status_code == 200, response.text
    assert response.json() == preview

    db.expire_all()
    left = {r.id for r in db.query(Recording).all()}
    assert left == kept
    for stored in mine_paths:
        assert not (settings.data_dir / stored).exists()
    for stored in kept_paths:
        assert (settings.data_dir / stored).is_file()
    documents = settings.data_dir / "documents"
    assert not (documents / f"subject-{history_id}").exists()
    assert (documents / f"subject-{other_id}").is_dir()
    assert db.query(Subject).count() == 1 and db.query(Round).count() == 1
    assert preview_of(client, other_id) == other_before
    assert db.execute(text("SELECT count(*) FROM usage_event")).scalar_one() == 1


def test_stale_fingerprint_409(client, db, settings):
    history = subject_with_history(db, settings)
    preview = preview_of(client, history["subject"].id)
    add_answered_round(db, settings, history, days_ago=1)
    response = client.delete(
        f"/api/subjects/{history['subject'].id}",
        params={"confirm": "true", "fingerprint": preview["fingerprint"]},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["preview"]["answers"] == 3 and detail["preview"]["rounds"] == 2
    assert detail["preview"]["fingerprint"] != preview["fingerprint"]
    assert db.query(Subject).count() == 1 and db.query(Recording).count() == 4
    assert history["document_file"].is_file()


def test_active_round_blocks_delete(client, db, settings):
    history = subject_with_history(db, settings)
    make_round(db, history["subject"].id, at=NOW, status="active")
    for params in ({"confirm": "true"}, {"confirm": "true", "fingerprint": "whatever"}):
        response = client.delete(f"/api/subjects/{history['subject'].id}", params=params)
        assert response.status_code == 409
        assert response.json()["detail"] == BUSY
    assert db.query(Subject).count() == 1

    paused = db.query(Round).filter_by(status="active").one()
    paused.status = "paused"
    db.commit()
    assert (
        client.delete(
            f"/api/subjects/{history['subject'].id}", params={"confirm": "true"}
        ).status_code
        == 200
    )


def test_fingerprint_is_optional_and_the_guard_applies(client, db, settings):
    history = subject_with_history(db, settings)
    preview = preview_of(client, history["subject"].id)
    bare = TestClient(create_app(settings))
    url = f"/api/subjects/{history['subject'].id}"
    assert bare.delete(url, params={"confirm": "true"}).status_code == 403
    assert db.query(Subject).count() == 1
    assert client.delete(url).status_code == 400
    response = client.delete(url, params={"confirm": "true"})
    assert response.status_code == 200
    assert response.json() == preview
    assert db.query(Document).count() == 0


def test_preview_budget_and_read_only(client, db, settings):
    history = subject_with_history(db, settings)
    url = f"/api/subjects/{history['subject'].id}/deletion-preview"

    def call():
        assert client.get(url).status_code == 200

    small = count_statements(client, call)
    for days_ago in range(2, 5):
        add_answered_round(db, settings, history, days_ago=days_ago)
    for _ in range(6):
        add_recording(db, settings)
    assert count_statements(client, call) == small
    assert writes_in(statements_of(call)) == []
