"""Giving up an answer that could not be processed (D4): who may, and what it leaves behind."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from test_assessment_support import point
from test_attempts_support import (
    ASSESS_TASK,
    FakeProviders,
    act,
    ai_for,
    answer_current,
    answered_attempt,
    override_providers,
    post_answer,
    post_audio,
    result,
    start_round,
)
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.attempts import pipeline
from app.features.attempts import service as attempts_service
from app.features.attempts.models import AttemptProcessing
from app.features.attempts.schemas import AttemptResultOut
from app.features.providers.fakes import FakeTextGenerator, FakeTranscriber
from app.features.providers.types import ProviderError
from app.features.rounds.models import Attempt

CANNOT_RETRY = "This attempt cannot be retried."
NO_RECORDED_ANSWER = "This attempt has no recorded answer."
FOLLOW_UP = "A follow-up answer cannot be given up."
RUNNING = "This answer is still being processed."
NOT_FAILED = "Only an answer that could not be processed can be given up."


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    return make_world(db)


def provider_error(kind: str) -> ProviderError:
    return ProviderError(kind, "openai", f"Provider failure: {kind}.", retryable=True)


def use(client, **fakes) -> FakeProviders:
    fake = FakeProviders(**fakes)
    override_providers(client.app, fake)
    return fake


def band_values(value) -> list:
    """Every non-null "band" anywhere in a JSON value."""
    found = []
    if isinstance(value, dict):
        found += [v for k, v in value.items() if k == "band" and v is not None]
        for v in value.values():
            found += band_values(v)
    elif isinstance(value, list):
        for v in value:
            found += band_values(v)
    return found


def give_up(client, attempt_id: int):
    return client.post(f"/api/attempts/{attempt_id}/give-up")


def finish(client, rid: int) -> dict:
    """Ends a practice round waiting on its last feedback."""
    state = client.get(f"/api/rounds/{rid}").json()
    if state["status"] == "active" and state["phase"] == "feedback":
        state = act(client, rid, "next", "feedback")
    assert state["status"] == "finished", state
    return state


def failed_answer(client, world, timing: str = "practice", **fakes) -> tuple[int, int]:
    """A finished one-question round whose answer failed in the pipeline."""
    use(client, **fakes)
    rid, attempt = answered_attempt(client, world, timing)
    post_answer(client, attempt)
    assert result(client, attempt)["status"] == "failed"
    finish(client, rid)
    return rid, attempt


def stored(settings, attempt_id: int) -> tuple:
    """The attempt and its row as another session sees them."""
    with SessionLocal(settings.effective_database_url) as session:
        attempt = session.get(Attempt, attempt_id)
        row = session.scalar(
            select(AttemptProcessing).where(AttemptProcessing.attempt_id == attempt_id)
        )
        return (
            attempt.outcome,
            attempt.processing_status,
            row.status if row else None,
            row.stage if row else None,
        )


def refused(client, settings, attempt_id: int, detail: str) -> None:
    """409 with the detail, and nothing written."""
    before = stored(settings, attempt_id)
    response = give_up(client, attempt_id)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == detail
    assert stored(settings, attempt_id) == before


def assert_given_up(body: dict, attempt_id: int) -> None:
    assert body["attempt_id"] == attempt_id
    assert (body["outcome"], body["status"], body["stage"]) == (
        "given_up",
        "ready",
        "not_assessed",
    )
    assert body["failure"] is None
    assert (body["transcript"], body["metrics"], body["assessment"]) == (None, None, None)
    assert band_values(body) == []


def test_a_transcription_failure_is_given_up(client, world, db, settings):
    rid, attempt = failed_answer(
        client, world, transcriber=FakeTranscriber(error=provider_error("timeout"))
    )

    response = give_up(client, attempt)

    assert response.status_code == 200, response.text
    body = response.json()
    assert_given_up(body, attempt)
    assert body["recording"] is not None
    assert result(client, attempt) == body
    assert stored(settings, attempt) == ("given_up", "ready", "ready", "not_assessed")
    db.expire_all()
    row = db.scalar(select(AttemptProcessing).where(AttemptProcessing.attempt_id == attempt))
    assert row.failure_kind == "timeout"  # kept as a record

    retried = client.post(f"/api/attempts/{attempt}/retry")
    assert (retried.status_code, retried.json()["detail"]) == (409, CANNOT_RETRY)
    late = post_audio(client, attempt)
    assert (late.status_code, late.json()["detail"]) == (409, NO_RECORDED_ANSWER)
    recovered = client.post(f"/api/attempts/{attempt}/recover")
    assert recovered.status_code == 200 and recovered.json()["action"] == "none"
    again = give_up(client, attempt)
    assert (again.status_code, again.json()) == (200, body)

    state = client.get(f"/api/rounds/{rid}").json()
    latest = next(
        i["latest_attempt"] for i in state["items"] if i["latest_attempt"]["id"] == attempt
    )
    assert (latest["outcome"], latest["processing_status"]) == ("given_up", "ready")
    summary = client.get(f"/api/rounds/{rid}/summary").json()
    assert summary["provisional"] is False
    assert attempt not in summary["unresolved_attempt_ids"]
    assert summary["outcomes"] == {"given_up": 1}
    assert band_values(summary) == []


def test_an_assessment_failure_is_given_up(client, world):
    incomplete = ai_for(points=[point("p1")])  # p2 has no judgement: invalid output twice
    _, attempt = failed_answer(client, world, assessor=FakeTextGenerator({ASSESS_TASK: incomplete}))
    assert result(client, attempt)["failure"]["kind"] == "invalid_output"
    response = give_up(client, attempt)
    assert response.status_code == 200, response.text
    assert_given_up(response.json(), attempt)


def test_a_microphone_failure_without_a_row_is_given_up(client, world, settings):
    rid = start_round(client, world, "exam")["id"]
    act(client, rid, "question_ready", "question")
    state = act(client, rid, "start_answer", "preparation", start_reason="auto")
    attempt = state["current_attempt"]["id"]
    assert act(client, rid, "mic_failure", "recording")["status"] == "finished"
    assert result(client, attempt)["failure"]["kind"] == "microphone"

    response = give_up(client, attempt)

    assert response.status_code == 200, response.text
    assert_given_up(response.json(), attempt)
    assert response.json()["recording"] is None
    assert stored(settings, attempt) == ("given_up", "ready", None, None)


def test_an_answer_whose_audio_never_arrived_is_given_up(client, world, db, settings):
    rid, attempt = answered_attempt(client, world, "exam")
    row = db.get(Attempt, attempt)
    row.ended_at = datetime.now(UTC) - timedelta(seconds=attempts_service.AUDIO_WAIT_SECONDS + 1)
    db.commit()

    response = give_up(client, attempt)  # settles the missing audio first

    assert response.status_code == 200, response.text
    assert_given_up(response.json(), attempt)
    assert stored(settings, attempt) == ("given_up", "ready", "ready", "not_assessed")


def test_a_run_cut_off_by_a_restart_is_given_up(client, world, monkeypatch, settings):
    use(client)
    monkeypatch.setattr(pipeline, "schedule", lambda *args, **kwargs: None)
    rid, attempt = answered_attempt(client, world, "exam")
    post_answer(client, attempt)
    assert stored(settings, attempt)[2] == "processing"
    assert result(client, attempt)["failure"]["kind"] == "interrupted"

    response = give_up(client, attempt)

    assert response.status_code == 200, response.text
    assert_given_up(response.json(), attempt)


def test_an_open_round_is_not_refused(client, world, settings):
    use(client, transcriber=FakeTranscriber(error=provider_error("timeout")))
    rid, attempt = answered_attempt(client, world, count=2)
    post_answer(client, attempt)
    assert result(client, attempt)["status"] == "failed"
    assert client.get(f"/api/rounds/{rid}").json()["status"] == "active"
    response = give_up(client, attempt)
    assert response.status_code == 200, response.text
    assert_given_up(response.json(), attempt)

    rid, attempt = answered_attempt(client, world, count=2)
    post_answer(client, attempt)
    assert result(client, attempt)["status"] == "failed"
    act(client, rid, "pause", "feedback")
    response = give_up(client, attempt)
    assert response.status_code == 200, response.text
    assert_given_up(response.json(), attempt)


def test_an_answer_that_is_not_failed_is_refused(client, world, settings):
    use(client)
    rid, ready = answered_attempt(client, world, "exam")
    post_answer(client, ready)
    assert result(client, ready)["status"] == "ready"
    refused(client, settings, ready, NOT_FAILED)

    rid = start_round(client, world, "exam")["id"]
    act(client, rid, "question_ready", "question")
    dont_know = act(client, rid, "dont_know", "preparation")["items"][0]["latest_attempt"]["id"]
    refused(client, settings, dont_know, NOT_FAILED)

    rid = start_round(client, world, "exam")["id"]
    skipped = act(client, rid, "skip", "question")["items"][0]["latest_attempt"]["id"]
    refused(client, settings, skipped, NOT_FAILED)


def test_a_live_run_is_refused(client, world, monkeypatch, settings):
    _, attempt = failed_answer(
        client, world, transcriber=FakeTranscriber(error=provider_error("timeout"))
    )
    monkeypatch.setattr(pipeline, "is_live", lambda attempt_id: True)
    refused(client, settings, attempt, RUNNING)


def test_a_retry_waiting_to_start_is_refused(client, world, db, monkeypatch, settings):
    _, attempt = failed_answer(
        client, world, transcriber=FakeTranscriber(error=provider_error("timeout"))
    )
    attempts_service.prepare_retry(db, attempt)
    monkeypatch.setattr(pipeline, "is_live", lambda attempt_id: True)  # scheduled
    refused(client, settings, attempt, RUNNING)
    assert stored(settings, attempt)[2] == "processing"


def test_a_follow_up_answer_is_refused(client, world, db, settings):
    class SecondCallFails(FakeTranscriber):
        def transcribe(self, *args, **kwargs):
            self.error = provider_error("timeout") if len(self.calls) == 1 else None
            return super().transcribe(*args, **kwargs)

    use(client, transcriber=SecondCallFails())
    rid = start_round(client, world, follow_ups={"enabled": True})["id"]
    main = answer_current(client, rid)
    post_answer(client, main)
    state = client.get(f"/api/rounds/{rid}").json()
    assert state["part"] == "followup", state
    follow = answer_current(client, rid)
    post_answer(client, follow)
    assert result(client, follow)["status"] == "failed"
    finish(client, rid)
    db.expire_all()
    assert db.get(Attempt, follow).attempt_kind == "followup"

    refused(client, settings, follow, FOLLOW_UP)


def test_an_unknown_attempt_is_not_found(client, db):
    response = give_up(client, 9999)
    assert (response.status_code, response.json()["detail"]) == (404, "Attempt not found.")


def test_an_ended_early_round_stays_provisional(client, world):
    use(client, transcriber=FakeTranscriber(error=provider_error("timeout")))
    rid, attempt = answered_attempt(client, world, count=2)
    post_answer(client, attempt)
    act(client, rid, "abandon", "feedback")

    assert give_up(client, attempt).status_code == 200

    summary = client.get(f"/api/rounds/{rid}/summary").json()
    assert summary["status"] == "abandoned"
    assert summary["provisional"] is True
    assert summary["unresolved_attempt_ids"] == []


def test_the_row_check_wins_a_race_with_a_retry(client, world, db, monkeypatch, settings):
    _, attempt = failed_answer(
        client, world, transcriber=FakeTranscriber(error=provider_error("timeout"))
    )
    seen_failed = result(client, attempt)
    row = db.scalar(select(AttemptProcessing).where(AttemptProcessing.attempt_id == attempt))
    row.status, row.stage = "ready", "done"  # a retried run has just succeeded
    db.commit()
    before = stored(settings, attempt)
    assert before[:2] == ("technical_failure", "failed")

    assert seen_failed["status"] == "failed"
    with monkeypatch.context() as patched:
        patched.setattr(
            attempts_service,
            "get_result",
            lambda session, attempt_id: AttemptResultOut.model_validate(seen_failed),
        )
        response = give_up(client, attempt)
    assert (response.status_code, response.json()["detail"]) == (409, RUNNING)
    assert stored(settings, attempt) == before

    refused(client, settings, attempt, NOT_FAILED)
