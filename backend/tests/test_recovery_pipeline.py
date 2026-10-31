"""Recovery of cut-off processing and late audio: no attempt is created, no paid call repeats."""

from datetime import UTC, datetime, timedelta

import pytest
from test_attempts_support import (
    FakeProviders,
    act,
    answer_current,
    answered_attempt,
    override_providers,
    post_answer,
    post_audio,
    post_events,
    result,
    start_round,
)
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.attempts import pipeline
from app.features.attempts.models import AttemptProcessing
from app.features.attempts.recovery import classify, uncertain_operation
from app.features.follow_ups.models import FollowUp
from app.features.rounds.models import Attempt


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    return make_world(db)


@pytest.fixture
def fake(client):
    fake = FakeProviders()
    override_providers(client.app, fake)
    return fake


@pytest.fixture
def held(monkeypatch):
    """Captures schedule() calls; each counts as live, as the real one does, but never runs."""
    calls: list[int] = []

    def capture(background, attempt_id, settings, providers):
        calls.append(attempt_id)
        pipeline._scheduled.add(attempt_id)

    monkeypatch.setattr(pipeline, "schedule", capture)
    yield calls
    for attempt_id in calls:
        pipeline._scheduled.discard(attempt_id)


def calls(fake) -> tuple[int, int, int]:
    return (
        len(fake.fake_transcriber.calls),
        len(fake.fake_assessor.calls),
        len(fake.fake_follow_up.calls),
    )


def recover(client, attempt_id: int):
    return client.post(f"/api/attempts/{attempt_id}/recover")


def row_of(db, attempt_id: int) -> AttemptProcessing:
    db.expire_all()
    return db.query(AttemptProcessing).filter_by(attempt_id=attempt_id).one()


def cut(db, attempt_id: int, **fields) -> None:
    """Leaves the row as a run cut off by a restart would have left it."""
    row = row_of(db, attempt_id)
    for key, value in fields.items():
        setattr(row, key, value)
    db.commit()


def snapshot(db, attempt_id: int) -> dict:
    row = row_of(db, attempt_id)
    return {c.name: getattr(row, c.name) for c in AttemptProcessing.__table__.columns}


def saved_answer(client, world, db, monkeypatch, timing="practice", count=1, **config):
    """An answer whose events and audio are stored but whose run never happened."""
    with monkeypatch.context() as m:
        m.setattr(pipeline, "schedule", lambda bg, a, s, p: None)
        rid = start_round(client, world, timing, count, **config)["id"]
        attempt_id = answer_current(client, rid)
        events, audio = post_answer(client, attempt_id)
        assert events.status_code == 200 and audio.status_code == 201
    return rid, attempt_id


def run_without_hand_off(monkeypatch, settings, attempt_id: int, providers) -> None:
    """A full run that stops between its two commits: the verdict is stored, the round not told."""
    with monkeypatch.context() as m:
        m.setattr(pipeline.rounds, "mark_attempt_result", lambda *a, **k: None)
        m.setattr(pipeline, "_prepare_follow_up", lambda *a, **k: None)
        pipeline.run(attempt_id, settings, providers)


def age(db, attempt_id: int, seconds: int) -> None:
    attempt = db.get(Attempt, attempt_id)
    attempt.ended_at = datetime.now(UTC) - timedelta(seconds=seconds)
    db.commit()


# ---- the pure classification ----


def _attempt(outcome="answered", processing="processing", recording=5) -> Attempt:
    return Attempt(id=7, outcome=outcome, processing_status=processing, recording_id=recording)


def _row(status="processing", stage="saved", **over) -> AttemptProcessing:
    fields = dict(
        attempt_id=7,
        status=status,
        stage=stage,
        events_json="{}",
        transcript_text=None,
        assessment_json=None,
        transcribe_tries=1,
        assess_tries=1,
    )
    fields.update(over)
    return AttemptProcessing(**fields)


@pytest.mark.parametrize(
    ("attempt", "row", "live", "expected"),
    [
        (_attempt("dont_know", None, None), None, False, "none"),
        (_attempt("interrupted", None, None), _row("ready", "not_assessed"), False, "none"),
        (_attempt(recording=None), None, False, "waiting_for_audio"),
        (_attempt(recording=None), _row(stage="waiting_for_audio"), False, "waiting_for_audio"),
        (_attempt(), _row(events_json=None), False, "waiting_for_audio"),
        (_attempt("technical_failure", None, None), None, False, "failed"),
        (_attempt(), _row(stage="analysing"), True, "running"),
        (_attempt(), _row(stage="saved"), False, "resumed"),
        (_attempt(), _row(stage="analysing"), False, "resumed"),
        (_attempt(), _row(stage="transcribing", transcript_text="tekst"), False, "resumed"),
        (_attempt(), _row(stage="transcribing"), False, "confirm"),
        (_attempt(), _row(stage="assessing", transcript_text="tekst"), False, "confirm"),
        (_attempt(), _row(stage="assessing", assessment_json="{}"), False, "resumed"),
        (_attempt(), _row("ready", "done", assessment_json="{}"), False, "completed"),
        (_attempt(), _row("failed", "transcribing", failure_kind="server"), False, "completed"),
        (
            _attempt("technical_failure", "failed"),
            _row("ready", "done", assessment_json="{}"),
            False,
            "completed",
        ),
        (
            _attempt("technical_failure", "failed"),
            _row("failed", "transcribing", failure_kind="server"),
            False,
            "failed",
        ),
        (_attempt("answered", "ready"), _row("ready", "done"), False, "none"),
    ],
)
def test_classify_table(attempt, row, live, expected):
    assert classify(attempt, row, live) == expected


def test_uncertain_operation_names_the_current_try():
    assert uncertain_operation(_row(stage="transcribing", transcribe_tries=2)) == (
        "attempt-7:transcribe:2"
    )
    assert uncertain_operation(_row(stage="assessing", assess_tries=3)) == "attempt-7:assess:3"
    assert uncertain_operation(_row(stage="analysing")) is None


# ---- through the API ----


def test_safe_cut_off_resumes_without_a_second_call(client, world, db, fake, monkeypatch):
    rid, attempt_id = saved_answer(client, world, db, monkeypatch)
    cut(db, attempt_id, stage="analysing")
    response = recover(client, attempt_id)
    assert response.status_code == 202, response.text
    assert response.json() == {"attempt_id": attempt_id, "action": "resumed", "operation_id": None}
    assert result(client, attempt_id)["status"] == "ready"
    assert calls(fake)[:2] == (1, 1)


def test_cut_after_transcript_runs_only_the_assessment(client, world, db, fake, monkeypatch):
    rid, attempt_id = saved_answer(client, world, db, monkeypatch)
    cut(db, attempt_id, stage="transcribing", transcribe_tries=1, transcript_text="Odpowiedź.")
    assert recover(client, attempt_id).json()["action"] == "resumed"
    body = result(client, attempt_id)
    assert (body["status"], body["transcript"]) == ("ready", "Odpowiedź.")
    assert calls(fake)[:2] == (0, 1)


def test_uncertain_call_needs_confirmation(client, world, db, fake, monkeypatch, held):
    rid, attempt_id = saved_answer(client, world, db, monkeypatch)
    cut(db, attempt_id, stage="transcribing", transcribe_tries=1)
    before = snapshot(db, attempt_id)
    response = recover(client, attempt_id)
    assert response.status_code == 200
    assert response.json() == {
        "attempt_id": attempt_id,
        "action": "confirm",
        "operation_id": f"attempt-{attempt_id}:transcribe:1",
    }
    assert held == []
    assert snapshot(db, attempt_id) == before
    monkeypatch.undo()
    assert client.post(f"/api/attempts/{attempt_id}/retry").status_code == 202
    assert [c["operation_id"] for c in fake.fake_transcriber.calls] == [
        f"attempt-{attempt_id}:transcribe:2"
    ]
    assert result(client, attempt_id)["status"] == "ready"


def test_uncertain_assessment_confirms(client, world, db, fake, monkeypatch, held):
    rid, attempt_id = saved_answer(client, world, db, monkeypatch)
    cut(db, attempt_id, stage="assessing", transcript_text="Odpowiedź.", assess_tries=1)
    body = recover(client, attempt_id).json()
    assert (body["action"], body["operation_id"]) == ("confirm", f"attempt-{attempt_id}:assess:1")
    assert held == [] and calls(fake) == (0, 0, 0)


def test_finished_run_is_handed_off(client, world, db, fake, monkeypatch, settings):
    rid, attempt_id = saved_answer(client, world, db, monkeypatch)
    run_without_hand_off(monkeypatch, settings, attempt_id, fake)
    assert client.get(f"/api/rounds/{rid}").json()["phase"] == "processing"
    before = calls(fake)
    assert recover(client, attempt_id).json()["action"] == "completed"
    assert client.get(f"/api/rounds/{rid}").json()["phase"] == "feedback"
    assert calls(fake) == before
    assert recover(client, attempt_id).json()["action"] == "none"


def test_finished_run_prepares_and_generates_the_follow_up_once(
    client, world, db, fake, monkeypatch, settings
):
    rid, attempt_id = saved_answer(
        client, world, db, monkeypatch, "practice", 2, follow_ups={"enabled": True}
    )
    run_without_hand_off(monkeypatch, settings, attempt_id, fake)
    assert db.query(FollowUp).count() == 0
    assert recover(client, attempt_id).json()["action"] == "completed"
    assert calls(fake) == (1, 1, 1)
    db.expire_all()
    assert db.query(FollowUp).one().status == "ready"
    state = client.get(f"/api/rounds/{rid}").json()
    assert (state["part"], state["phase"]) == ("followup", "question")
    assert recover(client, attempt_id).json()["action"] == "none"
    assert calls(fake) == (1, 1, 1)


def run_without_settle(monkeypatch, settings, attempt_id: int, providers) -> None:
    """A full run that stops after `mark_attempt_result` commits the attempt's status but before
    the round is settled (and before the follow-up's first try)."""
    with monkeypatch.context() as m:
        m.setattr(pipeline.rounds, "settle_round", lambda *a, **k: False)
        m.setattr(pipeline, "_follow_up", lambda *a, **k: None)
        pipeline.run(attempt_id, settings, providers)


def test_settled_attempt_with_unsettled_round_is_completed(
    client, world, db, fake, monkeypatch, settings
):
    rid, attempt_id = saved_answer(client, world, db, monkeypatch)
    run_without_settle(monkeypatch, settings, attempt_id, fake)
    db.expire_all()
    assert db.get(Attempt, attempt_id).processing_status == "ready"
    assert client.get(f"/api/rounds/{rid}").json()["phase"] == "processing"
    before = calls(fake)
    assert recover(client, attempt_id).json()["action"] == "completed"
    assert client.get(f"/api/rounds/{rid}").json()["phase"] == "feedback"
    assert calls(fake) == before
    assert recover(client, attempt_id).json()["action"] == "none"


def test_unsettled_round_waiting_for_the_follow_up_generates_it_once(
    client, world, db, fake, monkeypatch, settings
):
    rid, attempt_id = saved_answer(
        client, world, db, monkeypatch, "practice", 2, follow_ups={"enabled": True}
    )
    run_without_settle(monkeypatch, settings, attempt_id, fake)
    db.expire_all()
    assert db.query(FollowUp).one().tries == 0
    assert recover(client, attempt_id).json()["action"] == "completed"
    assert calls(fake) == (1, 1, 1)
    state = client.get(f"/api/rounds/{rid}").json()
    assert (state["part"], state["phase"]) == ("followup", "question")
    assert recover(client, attempt_id).json()["action"] == "none"
    assert calls(fake) == (1, 1, 1)


def test_live_run_is_left_alone(client, world, db, fake, monkeypatch, held):
    rid, attempt_id = saved_answer(client, world, db, monkeypatch)
    pipeline._active.add(attempt_id)
    try:
        assert recover(client, attempt_id).json()["action"] == "running"
    finally:
        pipeline._active.discard(attempt_id)
    assert held == []


def test_recover_is_idempotent(client, world, db, fake, monkeypatch, held):
    rid, attempt_id = saved_answer(client, world, db, monkeypatch)
    cut(db, attempt_id, stage="analysing")
    assert recover(client, attempt_id).json()["action"] == "resumed"
    assert recover(client, attempt_id).json()["action"] == "running"
    assert held == [attempt_id]


def test_round_recover_covers_every_unresolved_attempt(
    client, world, db, fake, monkeypatch, settings, held
):
    with monkeypatch.context() as m:
        m.setattr(pipeline, "schedule", lambda bg, a, s, p: None)
        rid = start_round(client, world, "exam", 4)["id"]
        ids = []
        for _ in range(4):
            attempt_id = answer_current(client, rid)
            post_answer(client, attempt_id)
            ids.append(attempt_id)
    safe, uncertain, finished, ready = ids
    cut(db, safe, stage="analysing")
    cut(db, uncertain, stage="transcribing", transcribe_tries=1)
    run_without_hand_off(monkeypatch, settings, finished, fake)
    pipeline.run(ready, settings, fake)
    response = client.post(f"/api/rounds/{rid}/recover")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "round_id": rid,
        "attempts": [
            {"attempt_id": safe, "action": "resumed", "operation_id": None},
            {
                "attempt_id": uncertain,
                "action": "confirm",
                "operation_id": f"attempt-{uncertain}:transcribe:1",
            },
            {"attempt_id": finished, "action": "completed", "operation_id": None},
            {"attempt_id": ready, "action": "none", "operation_id": None},
        ],
    }
    assert held == [safe]
    db.expire_all()
    assert db.query(Attempt).count() == 4  # recovery never creates an attempt


def test_recover_unknown_and_pending(client, world, fake):
    response = recover(client, 999)
    assert (response.status_code, response.json()["detail"]) == (404, "Attempt not found.")
    response = client.post("/api/rounds/999/recover")
    assert (response.status_code, response.json()["detail"]) == (404, "Round not found.")
    rid = start_round(client, world)["id"]
    pending = act(client, rid, "question_ready", "question")["current_attempt"]["id"]
    response = recover(client, pending)
    assert (response.status_code, response.json()["detail"]) == (
        409,
        "This attempt has no answer yet.",
    )


@pytest.mark.parametrize("audio_first", [False, True])
def test_late_audio_reopens_missing_audio_failure(client, world, db, fake, audio_first):
    rid, attempt_id = answered_attempt(client, world)
    age(db, attempt_id, 200)
    assert result(client, attempt_id)["failure"]["kind"] == "audio_missing"
    if audio_first:
        assert post_audio(client, attempt_id).status_code == 201
        assert result(client, attempt_id)["status"] == "failed"  # still waiting for the events
        assert post_events(client, attempt_id).status_code == 200
    else:
        assert post_events(client, attempt_id).status_code == 200
        assert post_audio(client, attempt_id).status_code == 201
    body = result(client, attempt_id)
    assert body["status"] == "ready" and body["assessment"]["band"] is not None
    assert client.post(f"/api/attempts/{attempt_id}/retry").status_code == 409
    assert len(fake.fake_transcriber.calls) == 1


@pytest.mark.parametrize("stage", ["transcribing", "assessing"])
def test_replayed_upload_never_restarts_an_uncertain_call(
    client, world, db, fake, monkeypatch, stage
):
    """The outbox replays an answer the server already had: an uncertain cut-off still waits."""
    rid, attempt_id = saved_answer(client, world, db, monkeypatch)
    fields = {"stage": stage, "transcribe_tries": 1}
    if stage == "assessing":
        fields.update(transcript_text="Odpowiedź.", assess_tries=1)
    cut(db, attempt_id, **fields)
    before = snapshot(db, attempt_id)
    events, audio = post_answer(client, attempt_id)
    assert events.status_code == 200 and audio.status_code == 200
    assert calls(fake) == (0, 0, 0)
    assert snapshot(db, attempt_id) == before
    assert recover(client, attempt_id).json()["action"] == "confirm"


def test_round_of_an_attempt(client, world, db, fake, monkeypatch):
    """What the browser's outbox checks before replaying a stored answer."""
    rid, attempt_id = saved_answer(client, world, db, monkeypatch)
    response = client.get(f"/api/attempts/{attempt_id}/round")
    assert response.status_code == 200, response.text
    assert response.json() == {"attempt_id": attempt_id, "round_id": rid}
    assert client.get(f"/api/attempts/{attempt_id + 1000}/round").status_code == 404
