"""A given-up answer's recording under the retention policies (owner ruling 2026-10-26)."""

from datetime import timedelta

import pytest
from test_attempts_support import (
    FakeProviders,
    answered_attempt,
    override_providers,
    post_answer,
    result,
)
from test_backup_support import NOW, add_recording, subject_with_history
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.attempts.models import AttemptProcessing
from app.features.providers.fakes import FakeTranscriber
from app.features.providers.types import ProviderError
from app.features.recording import retention
from app.features.recording.models import Recording
from app.features.rounds.models import Attempt


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def _ids(selection) -> set[int]:
    return set(selection.recording_ids)


def given_up_history(db, settings) -> dict:
    """The history's first answer given up, as give_up leaves it."""
    history = subject_with_history(db, settings)
    attempt = history["attempts"][0]
    attempt.outcome, attempt.processing_status = "given_up", "ready"
    row = db.query(AttemptProcessing).filter_by(attempt_id=attempt.id).one()
    row.status, row.stage = "ready", "not_assessed"
    db.commit()
    history["given_up"], history["assessed"] = history["recordings"]
    return history


def select(db, **kwargs):
    return retention.select_recordings(db, now=NOW, **kwargs)


def test_until_assessed_keeps_a_given_up_recording(db, settings):
    history = given_up_history(db, settings)

    selection = select(db, scope="policy", policy="assessed")

    assert history["given_up"].id not in _ids(selection)
    assert {history["assessed"].id, history["mic"].id} <= _ids(selection)
    assert selection.kept_unresolved == 0


def test_the_age_policies_delete_it_as_usual(db, settings):
    history = given_up_history(db, settings)
    given_up = history["given_up"]

    given_up.created_at = NOW - timedelta(days=40)
    db.commit()
    assert given_up.id in _ids(select(db, scope="policy", policy="30d"))
    assert given_up.id not in _ids(select(db, scope="policy", policy="90d"))
    given_up.created_at = NOW - timedelta(days=100)
    db.commit()
    assert given_up.id in _ids(select(db, scope="policy", policy="90d"))
    assert _ids(select(db, scope="policy", policy="forever")) == set()


def test_manual_deletions_include_it(db, settings):
    history = given_up_history(db, settings)
    given_up = history["given_up"].id

    assert given_up in _ids(select(db, scope="all"))
    assert given_up in _ids(select(db, scope="older_than", days=0))
    assert given_up in _ids(select(db, scope="subject", subject_id=history["subject"].id))


def test_the_sweep_keeps_it(db, settings):
    history = given_up_history(db, settings)
    settings.audio_retention = "assessed"
    retention._last_sweep.clear()

    retention.sweep_if_due(settings, now=NOW)

    db.expire_all()
    given_up = db.get(Recording, history["given_up"].id)
    assessed = db.get(Recording, history["assessed"].id)
    assert given_up.audio_deleted_at is None
    assert (settings.data_dir / given_up.stored_path).is_file()
    assert assessed.audio_deleted_at is not None
    assert not (settings.data_dir / assessed.stored_path).exists()


def test_choosing_the_policy_keeps_a_real_given_up_answer(client, db, settings):
    world = make_world(db)
    error = ProviderError("timeout", "openai", "Provider failure: timeout.", retryable=True)
    override_providers(client.app, FakeProviders(transcriber=FakeTranscriber(error=error)))
    rid, attempt = answered_attempt(client, world)
    post_answer(client, attempt)
    assert result(client, attempt)["status"] == "failed"
    finished = client.post(
        f"/api/rounds/{rid}/actions", json={"action": "next", "from_phase": "feedback"}
    )
    assert finished.json()["status"] == "finished", finished.text
    assert client.post(f"/api/attempts/{attempt}/give-up").status_code == 200
    stale_mic = add_recording(db, settings, purpose="mic_check", age=timedelta(days=2))
    db.expire_all()
    recording = db.get(Recording, db.get(Attempt, attempt).recording_id)
    wav_file = settings.data_dir / recording.stored_path
    assert wav_file.is_file()

    preview = client.get("/api/data/retention/preview", params={"policy": "assessed"}).json()
    assert (preview["recordings"], preview["kept_unresolved"]) == (1, 0)
    done = client.put(
        "/api/data/retention", json={"policy": "assessed", "fingerprint": preview["fingerprint"]}
    )

    assert done.status_code == 200, done.text
    assert done.json()["deleted"] == 1
    assert wav_file.is_file()
    db.expire_all()
    assert db.get(Recording, stale_mic.id).audio_deleted_at is not None
    assert result(client, attempt)["recording"]["id"] == recording.id


def test_a_technical_failure_is_still_kept_and_counted(db, settings):
    history = given_up_history(db, settings)
    failed = history["attempts"][1]
    failed.outcome, failed.processing_status = "technical_failure", "failed"
    row = db.query(AttemptProcessing).filter_by(attempt_id=failed.id).one()
    row.status, row.stage, row.failure_kind = "failed", "transcription", "transcription"
    db.commit()

    selection = select(db, scope="all")

    assert history["assessed"].id not in _ids(selection)
    assert history["given_up"].id in _ids(selection)
    assert selection.kept_unresolved == 1
