""" "Continue" past a main answer whose run was cut off by a restart (no_follow_up, cut_off)."""

import pytest
from sqlalchemy import select
from test_attempts_support import (
    FakeProviders,
    act,
    answer_current,
    override_providers,
    post_answer,
    result,
    start_round,
)
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.attempts import pipeline
from app.features.attempts.models import AttemptProcessing
from app.features.follow_ups.models import FollowUp
from app.features.rounds.models import Attempt
from app.features.rounds.state import MachineState, TransitionError, settle, transition

NOT_NOW = "This action is not available right now."
PAUSED = "The round is paused."


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    return make_world(db)


def use(client, **fakes) -> FakeProviders:
    fake = FakeProviders(**fakes)
    override_providers(client.app, fake)
    return fake


def state(client, rid: int) -> dict:
    return client.get(f"/api/rounds/{rid}").json()


def raw_act(client, rid: int, action: str, from_phase: str):
    return client.post(
        f"/api/rounds/{rid}/actions", json={"action": action, "from_phase": from_phase}
    )


def stored(settings, attempt_id: int) -> tuple:
    with SessionLocal(settings.effective_database_url) as session:
        attempt = session.get(Attempt, attempt_id)
        row = session.scalar(
            select(AttemptProcessing).where(AttemptProcessing.attempt_id == attempt_id)
        )
        return attempt.outcome, attempt.processing_status, row.status if row else None


def where(body: dict) -> tuple:
    return body["status"], body["phase"], body["current_index"]


def summary(client, rid: int) -> dict:
    return client.get(f"/api/rounds/{rid}/summary").json()


def cut_off(client, world, monkeypatch, timing: str = "practice", count: int = 2, **config):
    use(client)
    monkeypatch.setattr(pipeline, "schedule", lambda *args, **kwargs: None)
    rid = start_round(client, world, timing, count, **config)["id"]
    attempt = answer_current(client, rid)
    post_answer(client, attempt)
    seen = result(client, attempt)
    assert (seen["status"], seen["failure"]["kind"]) == ("failed", "interrupted")
    assert (state(client, rid)["phase"], state(client, rid)["part"]) == ("processing", "main")
    return rid, attempt


def machine(timing: str, *, is_last: bool = False, part: str = "main") -> MachineState:
    return MachineState(
        status="active",
        phase="processing",
        paused_from_phase=None,
        feedback_timing=timing,
        is_last=is_last,
        part=part,
        follow_ups_on=True,
        cut_off=True,
    )


def test_the_machine_accepts_no_follow_up_for_a_cut_off_answer():
    practice = transition(machine("practice"), "no_follow_up")
    assert (practice.phase, practice.advance) == ("feedback", False)
    exam = transition(machine("exam"), "no_follow_up")
    assert (exam.phase, exam.advance) == ("question", True)
    assert transition(machine("exam", is_last=True), "no_follow_up").status == "finished"


def test_a_cut_off_answer_never_settles_by_itself():
    assert settle(machine("practice")) is None
    assert settle(machine("exam")) is None


def test_the_follow_up_part_is_still_refused():
    with pytest.raises(TransitionError) as refused:
        transition(machine("practice", part="followup"), "no_follow_up")
    assert refused.value.message == NOT_NOW


def test_practice_continue_goes_to_feedback_and_stays_unresolved(
    client, world, monkeypatch, settings
):
    rid, attempt = cut_off(client, world, monkeypatch)
    before = stored(settings, attempt)
    assert before == ("answered", "processing", "processing")

    assert where(act(client, rid, "no_follow_up", "processing")) == ("active", "feedback", 0)

    seen = result(client, attempt)
    assert (seen["status"], seen["failure"]["kind"]) == ("failed", "interrupted")
    assert stored(settings, attempt) == before
    assert where(act(client, rid, "next", "feedback")) == ("active", "question", 1)
    body = summary(client, rid)
    assert attempt in body["unresolved_attempt_ids"]
    assert body["provisional"] is True


def test_exam_continue_goes_to_the_next_question(client, world, db, monkeypatch):
    rid, attempt = cut_off(client, world, monkeypatch, "exam", 2, follow_ups={"enabled": True})

    body = act(client, rid, "no_follow_up", "processing")

    assert where(body) == ("active", "question", 1)
    assert body["part"] == "main"
    item_id = db.get(Attempt, attempt).round_item_id
    assert db.scalars(select(FollowUp).where(FollowUp.main_item_id == item_id)).all() == []


def test_exam_continue_on_the_last_question_finishes(client, world, monkeypatch):
    rid, attempt = cut_off(client, world, monkeypatch, "exam", 1, follow_ups={"enabled": True})

    assert act(client, rid, "no_follow_up", "processing")["status"] == "finished"

    body = summary(client, rid)
    assert attempt in body["unresolved_attempt_ids"]
    assert body["provisional"] is True
    retried = client.post(f"/api/attempts/{attempt}/retry")
    assert retried.status_code == 202, retried.text


def assert_refused(client, rid: int, detail: str) -> None:
    before = state(client, rid)
    response = raw_act(client, rid, "no_follow_up", "processing")
    assert (response.status_code, response.json()["detail"]) == (409, detail)
    assert where(state(client, rid)) == where(before)


def test_a_live_run_is_still_refused(client, world, monkeypatch):
    rid, _ = cut_off(client, world, monkeypatch)
    monkeypatch.setattr(pipeline, "is_live", lambda attempt_id: True)
    assert_refused(client, rid, NOT_NOW)


def test_an_answer_waiting_for_its_audio_is_still_refused(client, world):
    use(client)
    rid = start_round(client, world, "practice", 2)["id"]
    answer_current(client, rid)
    assert state(client, rid)["phase"] == "processing"
    assert_refused(client, rid, NOT_NOW)


def test_a_paused_round_is_still_refused(client, world, monkeypatch):
    rid, _ = cut_off(client, world, monkeypatch)
    act(client, rid, "pause", "processing")
    assert_refused(client, rid, PAUSED)
