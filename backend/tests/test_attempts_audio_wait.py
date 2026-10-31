"""An answer whose audio never arrives becomes a technical failure after 120 s, never a score."""

from datetime import UTC, datetime, timedelta

import pytest
from test_attempts_failures import band_values
from test_attempts_support import (
    FakeProviders,
    act,
    answered_attempt,
    override_providers,
    post_audio,
    post_events,
    result,
    start_round,
)
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.attempts import service as attempts_service
from app.features.attempts.models import AttemptProcessing
from app.features.rounds.models import Attempt

MESSAGE = "The recording of this answer never reached the app, so it was not assessed."


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    return make_world(db)


def age(db, attempt_id: int, seconds: int) -> None:
    """Moves the answer's stop into the past instead of sleeping."""
    attempt = db.get(Attempt, attempt_id)
    attempt.ended_at = datetime.now(UTC) - timedelta(seconds=seconds)
    db.commit()


def test_waiting_under_limit_stays_processing(client, world, db):
    rid, attempt = answered_attempt(client, world)
    age(db, attempt, 119)
    body = result(client, attempt)
    assert (body["status"], body["stage"]) == ("processing", "waiting_for_audio")
    assert db.query(AttemptProcessing).count() == 0


def test_audio_missing_after_limit_is_failed_not_scored(client, world, db):
    rid, attempt = answered_attempt(client, world, count=2)
    age(db, attempt, 121)
    body = result(client, attempt)
    assert body["status"] == "failed"
    assert body["failure"] == {"kind": "audio_missing", "message": MESSAGE, "retryable": False}
    assert body["assessment"] is None and body["transcript"] is None and body["metrics"] is None
    assert band_values(body) == []
    state = client.get(f"/api/rounds/{rid}").json()
    latest = next(
        i["latest_attempt"] for i in state["items"] if i["latest_attempt"]["id"] == attempt
    )
    assert latest["outcome"] == "technical_failure"
    summary = client.get(f"/api/rounds/{rid}/summary").json()
    assert summary["provisional"] is True
    assert attempt in summary["unresolved_attempt_ids"]
    assert state["phase"] == "feedback"
    assert (
        client.post(
            f"/api/rounds/{rid}/actions", json={"action": "next", "from_phase": "feedback"}
        ).status_code
        == 200
    )


def test_mark_is_written_once(client, world, db):
    rid, attempt = answered_attempt(client, world)
    age(db, attempt, 200)
    first = result(client, attempt)
    second = result(client, attempt)
    assert first == second
    db.expire_all()
    assert db.query(AttemptProcessing).count() == 1
    assert db.get(Attempt, attempt).outcome == "technical_failure"


def test_late_upload_then_retry_processes_it(client, world, db):
    fake = FakeProviders()
    override_providers(client.app, fake)
    rid, attempt = answered_attempt(client, world)
    age(db, attempt, 200)
    assert result(client, attempt)["failure"]["kind"] == "audio_missing"
    assert post_events(client, attempt).status_code == 200
    assert post_audio(client, attempt).status_code == 201
    # Late audio reopens the answer by itself, so no manual retry is needed (or allowed)
    body = result(client, attempt)
    assert body["status"] == "ready" and body["assessment"]["band"] is not None
    assert client.post(f"/api/attempts/{attempt}/retry").status_code == 409
    assert len(fake.fake_transcriber.calls) == 1


def test_retry_without_audio_409(client, world, db):
    rid, attempt = answered_attempt(client, world)
    age(db, attempt, 200)
    result(client, attempt)
    assert client.post(f"/api/attempts/{attempt}/retry").status_code == 409
    post_events(client, attempt)
    assert client.post(f"/api/attempts/{attempt}/retry").status_code == 409


def test_events_only_still_marks(client, world, db):
    rid, attempt = answered_attempt(client, world)
    post_events(client, attempt)
    age(db, attempt, 200)
    body = result(client, attempt)
    assert body["failure"]["kind"] == "audio_missing"
    assert body["failure"]["retryable"] is False


def test_dont_know_and_interrupted_never_marked(client, world, db):
    rid = start_round(client, world, count=2)["id"]
    act(client, rid, "question_ready", "question")
    dk = act(client, rid, "dont_know", "preparation")["items"][0]["latest_attempt"]["id"]
    age(db, dk, 500)
    body = result(client, dk)
    assert body["status"] == "ready" and body["failure"] is None
    assert db.query(AttemptProcessing).count() == 0


def test_upload_claimed_between_read_and_mark_is_never_failed(client, world, db):
    rid, attempt = answered_attempt(client, world)
    age(db, attempt, 200)
    stale = db.get(Attempt, attempt)
    assert stale.recording_id is None  # read before the upload ...
    assert post_audio(client, attempt).status_code == 201  # ... which claims the recording
    assert stale.recording_id is None  # the session still holds the old row

    assert attempts_service.settle_missing_audio(db, stale) is False
    db.expire_all()
    row = db.query(AttemptProcessing).one()
    assert (row.status, row.failure_kind) == ("processing", None)
    fresh = db.get(Attempt, attempt)
    assert (fresh.outcome, fresh.processing_status) == ("answered", "processing")
    assert fresh.recording_id is not None
