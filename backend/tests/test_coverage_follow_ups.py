"""Coverage: a real follow-up never changes the map and never adds a whole-topic line."""

import json

import pytest
from sqlalchemy import delete, select
from test_attempts_support import (
    ASSESS_TASK,
    FOLLOW_UP_TEXT,
    FakeProviders,
    ai_for,
    answer_current,
    override_providers,
    post_answer,
    start_round,
)
from test_coverage_support import make_objective, make_objective_question
from test_topics_support import make_subject, make_topic, make_version, upgrade

from app.db.session import SessionLocal
from app.features.attempts.models import AttemptProcessing
from app.features.follow_ups.models import FollowUp
from app.features.providers.fakes import FakeTextGenerator
from app.features.questions.models import QuestionVersion
from app.features.rounds.models import Attempt, Round, RoundItem


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def get(client, path: str) -> dict:
    response = client.get(f"/api{path}")
    assert response.status_code == 200, response.text
    return response.json()


def graded() -> FakeTextGenerator:
    """The main answer is judged weaker than its follow-up, so a counted follow-up would show."""

    def judge(messages):
        follow = FOLLOW_UP_TEXT in messages[0].content
        return ai_for(statuses=["covered", "covered"] if follow else ["covered", "missing"])

    return FakeTextGenerator({ASSESS_TASK: judge})


def test_follow_up_never_changes_coverage(client, db):
    subject = make_subject(db, "Historia")
    _, passages = make_version(db, subject.id, count=2, excluded=())
    topic = make_topic(db, subject.id, "Ateny", passages)
    o1 = make_objective(db, topic, "O1", 0)
    o2 = make_objective(db, topic, "O2", 1)  # no question: a not-practised line of its own
    make_objective_question(
        db, subject.id, topic.id, o1.id, "Pytanie o O1?", passage_ids=tuple(p.id for p in passages)
    )
    override_providers(client.app, FakeProviders(assessor=graded()))

    rid = start_round(client, {"subject": subject}, "practice", 1, follow_ups={"enabled": True})[
        "id"
    ]
    main = answer_current(client, rid)
    post_answer(client, main)
    follow = answer_current(client, rid)  # the follow-up was ready: its question was asked
    post_answer(client, follow)
    response = client.post(
        f"/api/rounds/{rid}/actions", json={"action": "next", "from_phase": "feedback"}
    )
    assert response.status_code == 200, response.text

    # The follow-up is real: its own version, item and an answered, assessed attempt.
    db.expire_all()
    attempt = db.get(Attempt, follow)
    item = db.get(RoundItem, attempt.round_item_id)
    version = db.get(QuestionVersion, item.question_version_id)
    processing = db.scalar(select(AttemptProcessing).where(AttemptProcessing.attempt_id == follow))
    assert (attempt.attempt_kind, attempt.outcome, attempt.parent_attempt_id) == (
        "followup",
        "answered",
        main,
    )
    assert (item.role, version.status, version.text) == ("followup", "followup", FOLLOW_UP_TEXT)
    assert version.objective_id == o1.id  # the follow-up inherits the main question's objective
    assert processing.status == "ready"
    assessment = json.loads(processing.assessment_json)
    assert (assessment["status"], assessment["band"]) == ("assessed", "very_good")
    assert db.get(Round, rid).status == "finished"
    assert db.scalar(select(FollowUp.status).where(FollowUp.item_id == item.id)) == "ready"

    subject_after = get(client, f"/coverage/subjects/{subject.id}")
    topic_after = get(client, f"/coverage/topics/{topic.id}")

    # (c) Control: the main answer of this round counts, so the map is not empty.
    lines = {line["objective_id"]: line for line in topic_after["objectives"]}
    # No whole-topic line: the topic has objectives and no live question without one.
    assert list(lines) == [o1.id, o2.id]
    assert lines[o1.id]["answers"] == 1
    assert lines[o1.id]["deciding"]["attempt_id"] == main
    assert lines[o1.id]["deciding"]["band"] != "very_good"  # the main band, not the follow-up's
    assert lines[o1.id]["question_count"] == 1  # the follow-up version is no live question
    assert lines[o2.id]["answers"] == 0
    assert topic_after["question_count"] == 1
    assert [h["attempt_id"] for h in topic_after["history"]] == [main]
    assert subject_after["topics"][0]["objectives"] == topic_after["objectives"]

    # (b) Nothing in either payload points at the follow-up. The payloads name answers only by
    # attempt id (evidence, history) and questions only by their text (history), so: every
    # attempt id anywhere is the main one, and the follow-up's question text appears nowhere.
    def attempt_ids(value) -> set[int]:
        if isinstance(value, dict):
            own = {value["attempt_id"]} if "attempt_id" in value else set()
            return own.union(*(attempt_ids(v) for v in value.values()))
        if isinstance(value, list):
            return set().union(*(attempt_ids(v) for v in value))
        return set()

    assert attempt_ids(subject_after) == attempt_ids(topic_after) == {main}
    for body in (subject_after, topic_after):
        assert FOLLOW_UP_TEXT not in json.dumps(body, ensure_ascii=False)

    # (a) Rewind the follow-up to "asked but not answered yet". A running practice round keeps
    # its current main answer back until feedback, and feedback comes only after the follow-up,
    # so no live moment shows the main answer while the follow-up is still unanswered; resetting
    # the follow-up attempt gives that "before" state with everything else unchanged. No field
    # is excluded from the comparison: every timestamp in the payloads comes from the main
    # attempt, which the rewind does not touch.
    db.execute(delete(AttemptProcessing).where(AttemptProcessing.attempt_id == follow))
    attempt.outcome = "pending"
    attempt.processing_status = None
    attempt.ended_at = None
    db.commit()
    assert get(client, f"/coverage/subjects/{subject.id}") == subject_after
    assert get(client, f"/coverage/topics/{topic.id}") == topic_after
