"""The rounds service follows the follow-up part (no AI: follow-ups are saved by a helper)."""

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError
from test_attempts_support import act, answer_current, start_round
from test_rounds_support import answer_keys_in, make_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.follow_ups.models import FollowUp
from app.features.questions.models import QuestionFamily, QuestionVersion, RubricVersion
from app.features.rounds import service as rounds
from app.features.rounds.models import Attempt, RoundItem

FOLLOW_UP_TEXT = "Wyjaśnij, dlaczego ta procedura miała chronić demokrację przed tyranią."
ON = {"enabled": True}


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    return make_world(db)


def add_follow_up(
    session, main_attempt_id: int, status: str = "generating", failure_kind: str | None = None
) -> FollowUp:
    session.expire_all()
    main = session.get(Attempt, main_attempt_id)
    item = session.get(RoundItem, main.round_item_id)
    row = FollowUp(
        round_id=item.round_id,
        main_item_id=item.id,
        main_attempt_id=main.id,
        status=status,
        tries=1,
        started_at=datetime.now(UTC),
        failure_kind=failure_kind,
        failure_message="Fake provider failure." if failure_kind else None,
    )
    session.add(row)
    session.commit()
    return row


def save_ready_follow_up(session, main_attempt_id: int, text: str = FOLLOW_UP_TEXT) -> FollowUp:
    """The rows a generated follow-up becomes: question, rubric, item and a pending attempt."""
    session.expire_all()
    main = session.get(Attempt, main_attempt_id)
    item = session.get(RoundItem, main.round_item_id)
    version = session.get(QuestionVersion, item.question_version_id)
    family = session.get(QuestionFamily, version.family_id)
    rubric = session.scalar(
        select(RubricVersion).where(RubricVersion.question_version_id == version.id)
    )
    new_family = QuestionFamily(
        subject_id=family.subject_id, primary_topic_id=family.primary_topic_id
    )
    session.add(new_family)
    session.flush()
    new_version = QuestionVersion(
        family_id=new_family.id,
        version=1,
        text=text,
        style="short",
        difficulty=version.difficulty,
        source_policy=version.source_policy,
        objective_id=version.objective_id,
        secondary_topic_ids="[]",
        suggested_prep_s=15,
        suggested_answer_s=60,
        status="followup",
        provenance_json=json.dumps({"origin": "test"}),
    )
    session.add(new_version)
    session.flush()
    new_rubric = RubricVersion(question_version_id=new_version.id, rubric_json=rubric.rubric_json)
    session.add(new_rubric)
    session.flush()
    follow_item = RoundItem(
        round_id=item.round_id,
        ordinal=item.ordinal,
        role="followup",
        parent_item_id=item.id,
        question_version_id=new_version.id,
        rubric_version_id=new_rubric.id,
    )
    session.add(follow_item)
    session.flush()
    session.add(
        Attempt(
            round_item_id=follow_item.id,
            attempt_kind="followup",
            parent_attempt_id=main.id,
            outcome="pending",
        )
    )
    row = session.scalar(select(FollowUp).where(FollowUp.main_item_id == item.id))
    if row is None:
        row = FollowUp(
            round_id=item.round_id,
            main_item_id=item.id,
            main_attempt_id=main.id,
            tries=1,
            started_at=datetime.now(UTC),
            status="ready",
        )
        session.add(row)
    row.status = "ready"
    row.item_id = follow_item.id
    session.commit()
    return row


def mark_ready(session, attempt_id: int) -> dict:
    session.expire_all()
    return rounds.mark_attempt_result(session, attempt_id, "ready").model_dump(mode="json")


def state_of(client, round_id: int) -> dict:
    response = client.get(f"/api/rounds/{round_id}")
    assert response.status_code == 200, response.text
    return response.json()


def to_follow_up(client, db, world, timing: str = "practice", count: int = 2) -> tuple:
    """A round whose first main answer is ready and whose follow-up is being asked."""
    rid = start_round(client, world, timing, count, follow_ups=ON)["id"]
    main = answer_current(client, rid)
    add_follow_up(db, main)
    assert mark_ready(db, main)["phase"] == "processing"
    save_ready_follow_up(db, main)
    db.expire_all()
    rounds.settle_round(db, rid)
    return rid, main, state_of(client, rid)


def test_ready_follow_up_moves_round_to_follow_up_question(client, db, world):
    rid, main, state = to_follow_up(client, db, world)
    assert (state["part"], state["phase"], state["status"]) == ("followup", "question", "active")
    assert state["current"]["text"] == FOLLOW_UP_TEXT
    attempt = state["current_attempt"]
    assert (attempt["attempt_kind"], attempt["parent_attempt_id"], attempt["outcome"]) == (
        "followup",
        main,
        "pending",
    )
    assert state["total"] == 2
    assert [i["role"] for i in state["items"]] == ["main", "main"]
    assert state["follow_up"]["status"] == "ready"


def test_ready_follow_up_is_upcoming_until_asked(client, db, world):
    rid = start_round(client, world, "practice", 2, follow_ups=ON)["id"]
    main = answer_current(client, rid)
    add_follow_up(db, main)
    save_ready_follow_up(db, main)
    db.expire_all()
    waiting = state_of(client, rid)
    assert (waiting["part"], waiting["phase"]) == ("main", "processing")
    assert waiting["upcoming"][0] == FOLLOW_UP_TEXT
    assert len(waiting["upcoming"]) == 2  # then the next main question
    mark_ready(db, main)
    asked = state_of(client, rid)
    assert asked["part"] == "followup"
    assert FOLLOW_UP_TEXT not in asked["upcoming"]
    answer_current(client, rid)
    mark_ready(db, asked["current_attempt"]["id"])
    feedback = state_of(client, rid)
    assert (feedback["part"], feedback["phase"]) == ("main", "feedback")
    assert FOLLOW_UP_TEXT not in feedback["upcoming"]


def test_follow_up_answer_then_feedback_shows_main(client, db, world):
    rid, main, _ = to_follow_up(client, db, world)
    follow = answer_current(client, rid)
    state = state_of(client, rid)
    assert (state["part"], state["phase"]) == ("followup", "processing")
    assert state["current_attempt"]["id"] == follow
    state = mark_ready(db, follow)
    assert (state["part"], state["phase"]) == ("main", "feedback")
    assert state["current_attempt"]["id"] == main
    state = act(client, rid, "next", "feedback")
    assert (state["phase"], state["current_index"], state["part"]) == ("question", 1, "main")
    assert state["current_attempt"]["attempt_kind"] == "initial"
    assert state["follow_up"] is None


@pytest.mark.parametrize("action", ["dont_know", "skip"])
def test_follow_up_dont_know_and_skip_go_to_feedback(client, db, world, action):
    rid, main, _ = to_follow_up(client, db, world)
    act(client, rid, "question_ready", "question")
    state = act(client, rid, action, "preparation")
    assert (state["part"], state["phase"]) == ("main", "feedback")
    assert state["current_attempt"]["id"] == main
    db.expire_all()
    follow = db.scalar(select(Attempt).where(Attempt.attempt_kind == "followup"))
    assert follow.outcome == ("skipped" if action == "skip" else "dont_know")


def test_exam_follow_up_stop_goes_to_next_item(client, db, world):
    rid, main, state = to_follow_up(client, db, world, "exam")
    assert (state["part"], state["phase"]) == ("followup", "question")
    answer_current(client, rid)
    state = state_of(client, rid)
    assert (state["part"], state["phase"], state["current_index"]) == ("main", "question", 1)
    assert state["follow_up"] is None


def test_exam_main_result_without_follow_up_advances(client, db, world):
    rid = start_round(client, world, "exam", 1, follow_ups=ON)["id"]
    main = answer_current(client, rid)
    assert state_of(client, rid)["phase"] == "processing"
    state = mark_ready(db, main)
    assert (state["status"], state["phase"]) == ("finished", "summary")


def test_pause_in_follow_up_restarts_follow_up_not_main(client, db, world):
    rid, main, _ = to_follow_up(client, db, world)
    act(client, rid, "question_ready", "question")
    act(client, rid, "start_answer", "preparation", start_reason="auto")
    interrupted = state_of(client, rid)["current_attempt"]["id"]
    act(client, rid, "pause", "recording")
    state = act(client, rid, "resume", "recording")
    assert (state["part"], state["phase"]) == ("followup", "question")
    attempt = state["current_attempt"]
    assert (attempt["attempt_kind"], attempt["parent_attempt_id"]) == ("followup", interrupted)
    assert state["current"]["text"] == FOLLOW_UP_TEXT
    db.expire_all()
    assert db.get(Attempt, interrupted).outcome == "interrupted"
    follow_item = db.get(Attempt, attempt["id"]).round_item_id
    assert db.get(RoundItem, follow_item).role == "followup"
    assert db.query(FollowUp).count() == 1
    main_item = db.get(Attempt, main).round_item_id
    assert db.query(Attempt).filter(Attempt.round_item_id == main_item).count() == 1


def _paused_in_processing(client, db, world) -> tuple[int, int]:
    rid = start_round(client, world, "practice", 2, follow_ups=ON)["id"]
    main = answer_current(client, rid)
    act(client, rid, "pause", "processing")
    return rid, main


def test_resume_from_processing_rules(client, db, world):
    # The follow-up became ready while paused: resume asks it.
    rid, main = _paused_in_processing(client, db, world)
    add_follow_up(db, main)
    mark_ready(db, main)
    save_ready_follow_up(db, main)
    rounds.settle_round(db, rid)
    assert state_of(client, rid)["status"] == "paused"
    state = act(client, rid, "resume", "processing")
    assert (state["part"], state["phase"]) == ("followup", "question")

    # Still generating, or failed: back to processing.
    for status, kind in (("generating", None), ("failed", "server")):
        rid, main = _paused_in_processing(client, db, world)
        add_follow_up(db, main, status, kind)
        mark_ready(db, main)
        state = act(client, rid, "resume", "processing")
        assert (state["part"], state["phase"]) == ("main", "processing"), status
        assert state["follow_up"]["status"] in ("generating", "failed")

    # The main result is ready and there is no follow-up: as settle (practice feedback).
    rid, main = _paused_in_processing(client, db, world)
    mark_ready(db, main)
    state = act(client, rid, "resume", "processing")
    assert (state["part"], state["phase"]) == ("main", "feedback")

    # No result yet: processing.
    rid, main = _paused_in_processing(client, db, world)
    state = act(client, rid, "resume", "processing")
    assert (state["part"], state["phase"]) == ("main", "processing")


@pytest.mark.parametrize("timing", ["practice", "exam"])
@pytest.mark.parametrize("status,kind", [("generating", None), ("failed", "server")])
def test_no_follow_up_while_generating_and_failed(client, db, world, timing, status, kind):
    rid = start_round(client, world, timing, 2, follow_ups=ON)["id"]
    main = answer_current(client, rid)
    add_follow_up(db, main, status, kind)
    assert mark_ready(db, main)["phase"] == "processing"
    state = act(client, rid, "no_follow_up", "processing")
    if timing == "practice":
        assert (state["phase"], state["part"], state["current_attempt"]["id"]) == (
            "feedback",
            "main",
            main,
        )
    else:
        assert (state["phase"], state["current_index"]) == ("question", 1)
    db.expire_all()
    row = db.scalar(select(FollowUp))
    assert (row.status, row.failure_kind) == ("omitted", kind)
    again = act(client, rid, "no_follow_up", "processing")
    assert again == state


def test_no_follow_up_allowed_when_main_ready_without_row(client, db, world):
    rid = start_round(client, world, "practice", 2, follow_ups=ON)["id"]
    main = answer_current(client, rid)
    # The main result was stored but the round never moved on (e.g. the process stopped).
    db.expire_all()
    db.get(Attempt, main).processing_status = "ready"
    db.commit()
    assert state_of(client, rid)["phase"] == "processing"
    state = act(client, rid, "no_follow_up", "processing")
    assert (state["phase"], state["part"]) == ("feedback", "main")


def test_no_follow_up_refused_elsewhere(client, db, world):
    rid, _, _ = to_follow_up(client, db, world)
    response = client.post(
        f"/api/rounds/{rid}/actions", json={"action": "no_follow_up", "from_phase": "question"}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "This action is not available right now."


def test_state_has_no_rubric_or_model_answer(client, db, world):
    rid, _, state = to_follow_up(client, db, world)
    assert answer_keys_in(state) == set()
    act(client, rid, "question_ready", "question")
    assert answer_keys_in(state_of(client, rid)) == set()


def test_get_round_is_read_only(client, db, world):
    rid = start_round(client, world, "practice", 2, follow_ups=ON)["id"]
    main = answer_current(client, rid)
    add_follow_up(db, main)  # not live in this process: cut off
    mark_ready(db, main)
    state = state_of(client, rid)
    assert state["phase"] == "processing"
    assert state["follow_up"]["status"] == "failed"
    assert state["follow_up"]["failure"]["kind"] == "interrupted"
    assert state["follow_up"]["retryable"] is True
    db.expire_all()
    assert db.scalar(select(FollowUp)).status == "generating"


def test_settle_retries_on_stale_data(client, db, world, monkeypatch):
    rid = start_round(client, world, "practice", 2, follow_ups=ON)["id"]
    main = answer_current(client, rid)
    add_follow_up(db, main)
    mark_ready(db, main)
    save_ready_follow_up(db, main)
    original = db.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            raise StaleDataError("a parallel request won")
        original()

    monkeypatch.setattr(db, "commit", flaky_commit)
    db.expire_all()
    rounds.settle_round(db, rid)
    monkeypatch.undo()
    assert calls["n"] >= 2
    state = state_of(client, rid)
    assert (state["part"], state["phase"]) == ("followup", "question")


def test_settle_between_read_and_action_wins(client, db, world, settings, monkeypatch):
    """Settle bumps the version, so an action read before it loses with 409."""
    rid = start_round(client, world, "practice", 2, follow_ups=ON)["id"]
    main = answer_current(client, rid)
    add_follow_up(db, main)
    mark_ready(db, main)
    save_ready_follow_up(db, main)
    original = rounds._machine
    settled = []

    def settle_meanwhile(*args, **kwargs):
        machine = original(*args, **kwargs)
        if not settled:  # once, from the pause request; settle_round reads _machine itself
            settled.append(True)
            with SessionLocal(settings.effective_database_url) as other:
                rounds.settle_round(other, rid)
        return machine

    monkeypatch.setattr(rounds, "_machine", settle_meanwhile)
    response = client.post(
        f"/api/rounds/{rid}/actions", json={"action": "pause", "from_phase": "processing"}
    )
    monkeypatch.undo()
    assert response.status_code == 409
    state = state_of(client, rid)
    assert (state["status"], state["part"], state["phase"]) == ("active", "followup", "question")
    assert state["pause_count"] == 0
