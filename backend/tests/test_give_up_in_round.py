"""Giving up a failed answer while its round is still open (owner ruling 2026-10-26)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
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
from app.features.attempts import service as attempts_service
from app.features.attempts.models import AttemptProcessing
from app.features.follow_ups.models import FollowUp
from app.features.providers.fakes import FakeTranscriber
from app.features.providers.types import ProviderError
from app.features.rounds import service as rounds_service
from app.features.rounds.models import Attempt, RoundItem

CANNOT_RETRY = "This attempt cannot be retried."
NO_RECORDED_ANSWER = "This attempt has no recorded answer."
FOLLOW_UP = "A follow-up answer cannot be given up."
RUNNING = "This answer is still being processed."
NOT_NOW = "This action is not available right now."


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


def state(client, rid: int) -> dict:
    return client.get(f"/api/rounds/{rid}").json()


def raw_act(client, rid: int, action: str, from_phase: str):
    return client.post(
        f"/api/rounds/{rid}/actions", json={"action": action, "from_phase": from_phase}
    )


def summary(client, rid: int) -> dict:
    return client.get(f"/api/rounds/{rid}/summary").json()


def round_report(client, rid: int) -> dict:
    response = client.get(f"/api/reports/rounds/{rid}")
    assert response.status_code == 200, response.text
    return response.json()


def recover(client, attempt_id: int) -> str:
    response = client.post(f"/api/attempts/{attempt_id}/recover")
    assert response.status_code in (200, 202), response.text
    return response.json()["action"]


def where(body: dict) -> tuple:
    return body["status"], body["phase"], body["current_index"]


def given_up_ok(client, attempt_id: int) -> dict:
    response = give_up(client, attempt_id)
    assert response.status_code == 200, response.text
    assert_given_up(response.json(), attempt_id)
    return response.json()


def failing() -> FakeTranscriber:
    return FakeTranscriber(error=provider_error("timeout"))


class FailsFirstCall(FakeTranscriber):
    def transcribe(self, *args, **kwargs):
        self.error = provider_error("timeout") if not self.calls else None
        return super().transcribe(*args, **kwargs)


class FailsFromSecondCall(FakeTranscriber):
    def transcribe(self, *args, **kwargs):
        self.error = provider_error("timeout") if self.calls else None
        return super().transcribe(*args, **kwargs)


def cut_off(client, world, monkeypatch, timing: str = "practice", count: int = 2, **config):
    """A main answer whose run was cut off by a restart: the round waits in processing."""
    use(client)
    monkeypatch.setattr(pipeline, "schedule", lambda *args, **kwargs: None)
    rid = start_round(client, world, timing, count, **config)["id"]
    attempt = answer_current(client, rid)
    post_answer(client, attempt)
    seen = result(client, attempt)
    assert (seen["status"], seen["failure"]["kind"]) == ("failed", "interrupted")
    assert (state(client, rid)["phase"], state(client, rid)["part"]) == ("processing", "main")
    return rid, attempt


def practice_failure(client, world, count: int = 2, **config) -> tuple[int, int]:
    """A practice main answer that failed in the pipeline: the round shows its feedback."""
    use(client, transcriber=failing())
    rid = start_round(client, world, "practice", count, **config)["id"]
    attempt = answer_current(client, rid)
    post_answer(client, attempt)
    assert result(client, attempt)["status"] == "failed"
    assert where(state(client, rid)) == ("active", "feedback", 0)
    return rid, attempt


def test_practice_feedback_stays_on_the_item(client, world, settings):
    rid, attempt = practice_failure(client, world, immediate_repeat=True)

    given_up_ok(client, attempt)

    body = state(client, rid)
    assert where(body) == ("active", "feedback", 0)
    assert body["part"] == "main"
    current = body["current_attempt"]
    assert (current["outcome"], current["processing_status"]) == ("given_up", "ready")
    assert body["can_answer_again"] is False
    again = raw_act(client, rid, "answer_again", "feedback")
    assert (again.status_code, again.json()["detail"]) == (409, NOT_NOW)
    retried = client.post(f"/api/attempts/{attempt}/retry")
    assert (retried.status_code, retried.json()["detail"]) == (409, CANNOT_RETRY)
    assert where(act(client, rid, "next", "feedback")) == ("active", "question", 1)


def test_practice_microphone_failure(client, world, settings):
    use(client)
    rid = start_round(client, world, "practice", 2)["id"]
    act(client, rid, "question_ready", "question")
    attempt = act(client, rid, "start_answer", "preparation", start_reason="auto")[
        "current_attempt"
    ]["id"]
    assert act(client, rid, "mic_failure", "recording")["phase"] == "feedback"

    given_up_ok(client, attempt)

    assert stored(settings, attempt) == ("given_up", "ready", None, None)
    assert where(state(client, rid)) == ("active", "feedback", 0)


def test_practice_cut_off_moves_to_feedback(client, world, monkeypatch):
    rid, attempt = cut_off(client, world, monkeypatch)

    given_up_ok(client, attempt)

    assert where(state(client, rid)) == ("active", "feedback", 0)
    assert recover(client, attempt) == "none"


def test_exam_with_follow_ups_cut_off_moves_to_the_next_question(client, world, db, monkeypatch):
    rid, attempt = cut_off(client, world, monkeypatch, "exam", 2, follow_ups={"enabled": True})

    given_up_ok(client, attempt)

    body = state(client, rid)
    assert where(body) == ("active", "question", 1)
    assert body["part"] == "main"
    item_id = db.get(Attempt, attempt).round_item_id
    assert db.scalars(select(FollowUp).where(FollowUp.main_item_id == item_id)).all() == []


def test_exam_with_follow_ups_cut_off_on_the_last_question_finishes(client, world, monkeypatch):
    rid, attempt = cut_off(client, world, monkeypatch, "exam", 1, follow_ups={"enabled": True})

    given_up_ok(client, attempt)

    assert state(client, rid)["status"] == "finished"


def test_exam_without_follow_ups_an_earlier_item(client, world):
    use(client, transcriber=failing())
    rid = start_round(client, world, "exam", 2)["id"]
    first = answer_current(client, rid)
    before = state(client, rid)
    assert where(before) == ("active", "question", 1)
    post_answer(client, first)
    assert result(client, first)["status"] == "failed"

    given_up_ok(client, first)

    after = state(client, rid)
    assert where(after) == ("active", "question", 1)
    assert after["current_attempt"]["id"] == before["current_attempt"]["id"]
    use(client)
    second = answer_current(client, rid)
    post_answer(client, second)
    assert state(client, rid)["status"] == "finished"
    body = summary(client, rid)
    assert body["provisional"] is False
    assert body["outcomes"] == {"given_up": 1, "answered": 1}


def test_paused_from_processing_moves_on_resume(client, world, monkeypatch):
    rid, attempt = cut_off(client, world, monkeypatch)
    act(client, rid, "pause", "processing")

    given_up_ok(client, attempt)

    body = state(client, rid)
    assert (body["status"], body["phase"]) == ("paused", "processing")
    assert where(act(client, rid, "resume", "processing")) == ("active", "feedback", 0)


def test_a_lost_settle_is_repaired_by_recovery(client, world, monkeypatch):
    rid, attempt = cut_off(client, world, monkeypatch)
    with monkeypatch.context() as patched:
        patched.setattr(rounds_service, "settle_round", lambda *args, **kwargs: False)
        given_up_ok(client, attempt)
        assert state(client, rid)["phase"] == "processing"

    assert recover(client, attempt) == "completed"
    assert where(state(client, rid)) == ("active", "feedback", 0)


def test_a_lost_settle_is_repaired_by_a_second_give_up(client, world, monkeypatch):
    rid, attempt = cut_off(client, world, monkeypatch)
    with monkeypatch.context() as patched:
        patched.setattr(rounds_service, "settle_round", lambda *args, **kwargs: False)
        body = given_up_ok(client, attempt)
        assert state(client, rid)["phase"] == "processing"

    again = give_up(client, attempt)

    assert (again.status_code, again.json()) == (200, body)
    assert where(state(client, rid)) == ("active", "feedback", 0)


def overdue(db, attempt_id: int) -> None:
    row = db.get(Attempt, attempt_id)
    row.ended_at = datetime.now(UTC) - timedelta(seconds=attempts_service.AUDIO_WAIT_SECONDS + 1)
    db.commit()


def test_late_audio_after_a_mid_round_give_up(client, world, db, settings):
    use(client)
    rid, attempt = answered_attempt(client, world, "practice", 2)
    overdue(db, attempt)
    assert result(client, attempt)["failure"]["kind"] == "audio_missing"
    assert state(client, rid)["phase"] == "feedback"

    given_up_ok(client, attempt)

    late = post_events(client, attempt)
    assert (late.status_code, late.json()["detail"]) == (409, NO_RECORDED_ANSWER)
    late = post_audio(client, attempt)
    assert (late.status_code, late.json()["detail"]) == (409, NO_RECORDED_ANSWER)
    assert stored(settings, attempt) == ("given_up", "ready", "ready", "not_assessed")


def test_late_audio_first_is_running(client, world, db, monkeypatch, settings):
    use(client)
    rid, attempt = answered_attempt(client, world, "practice", 2)
    overdue(db, attempt)
    assert result(client, attempt)["failure"]["kind"] == "audio_missing"
    monkeypatch.setattr(pipeline, "schedule", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "is_live", lambda attempt_id: True)
    post_answer(client, attempt)
    assert stored(settings, attempt)[2] == "processing"

    refused(client, settings, attempt, RUNNING)


def test_a_retry_race_mid_round(client, world, db, monkeypatch, settings):
    rid, attempt = practice_failure(client, world)
    given_up_later = practice_failure(client, world)[1]

    given_up_ok(client, given_up_later)
    retried = client.post(f"/api/attempts/{given_up_later}/retry")
    assert (retried.status_code, retried.json()["detail"]) == (409, CANNOT_RETRY)

    attempts_service.prepare_retry(db, attempt)
    monkeypatch.setattr(pipeline, "is_live", lambda attempt_id: True)
    refused(client, settings, attempt, RUNNING)


def test_a_follow_up_answer_of_an_open_round_is_refused(client, world, db, settings):
    class SecondCallFails(FakeTranscriber):
        def transcribe(self, *args, **kwargs):
            self.error = provider_error("timeout") if len(self.calls) == 1 else None
            return super().transcribe(*args, **kwargs)

    use(client, transcriber=SecondCallFails())
    rid = start_round(client, world, follow_ups={"enabled": True})["id"]
    main = answer_current(client, rid)
    post_answer(client, main)
    assert state(client, rid)["part"] == "followup"
    follow = answer_current(client, rid)
    post_answer(client, follow)
    assert result(client, follow)["status"] == "failed"
    assert state(client, rid)["status"] == "active"
    db.expire_all()
    assert db.get(Attempt, follow).attempt_kind == "followup"

    refused(client, settings, follow, FOLLOW_UP)


def test_answer_again_given_up(client, world, db):
    use(client, transcriber=FailsFromSecondCall())
    rid = start_round(client, world, "practice", 1, immediate_repeat=True)["id"]
    main = answer_current(client, rid)
    post_answer(client, main)
    assert result(client, main)["status"] == "ready"
    act(client, rid, "answer_again", "feedback")
    repeat = answer_current(client, rid)
    post_answer(client, repeat)
    assert result(client, repeat)["status"] == "failed"
    assert state(client, rid)["phase"] == "feedback"
    db.expire_all()
    assert db.get(Attempt, repeat).attempt_kind == "immediate"

    given_up_ok(client, repeat)

    assert act(client, rid, "next", "feedback")["status"] == "finished"
    body = summary(client, rid)
    assert body["provisional"] is False
    assert body["outcomes"] == {"answered": 1}
    repeats = round_report(client, rid)["repeats"]["knowledge"]
    assert (repeats["not_assessed"], repeats["unresolved"]) == (1, 0)
    assert band_values(result(client, repeat)) == []


def test_a_round_whose_failure_was_given_up_is_not_provisional(client, world):
    use(client, transcriber=FailsFirstCall())
    rid = start_round(client, world, "practice", 2)["id"]
    first = answer_current(client, rid)
    post_answer(client, first)
    assert result(client, first)["status"] == "failed"
    given_up_ok(client, first)
    act(client, rid, "next", "feedback")
    second = answer_current(client, rid)
    post_answer(client, second)
    assert result(client, second)["status"] == "ready"
    assert act(client, rid, "next", "feedback")["status"] == "finished"

    body = summary(client, rid)
    assert body["provisional"] is False
    assert body["unresolved_attempt_ids"] == []
    assert body["outcomes"] == {"given_up": 1, "answered": 1}
    report = round_report(client, rid)
    assert (report["knowledge"]["not_assessed"], report["knowledge"]["unresolved"]) == (1, 0)
    assert report["provisional"] is False


def test_abandoned_after_a_mid_round_give_up_stays_provisional(client, world):
    rid, attempt = practice_failure(client, world)
    given_up_ok(client, attempt)

    act(client, rid, "abandon", "feedback")

    body = summary(client, rid)
    assert body["provisional"] is True
    assert body["unresolved_attempt_ids"] == []


def test_the_item_counts_as_done(client, world, db):
    rid, attempt = practice_failure(client, world)
    given_up_ok(client, attempt)
    item_id = db.get(Attempt, attempt).round_item_id
    assert db.get(RoundItem, item_id) is not None
    item = next(i for i in act(client, rid, "next", "feedback")["items"] if i["id"] == item_id)
    assert item["status"] == "done"
