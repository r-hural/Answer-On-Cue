"""Answer again: one immediate repeat of a practice main question, linked and apart."""

import pytest
from test_attempts_support import act, answer_current, post_answer
from test_repetition_support import (
    answer_again,
    answer_ready,
    get,
    repeat_world,
    start_practice,
    state,
    use_scripted,
)
from test_rounds_support import answer_keys_in
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.rounds import service as rounds
from app.features.rounds.models import Attempt, RoundItem


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    return repeat_world(db)


def attempts_of(db, item_id: int) -> list[Attempt]:
    db.expire_all()
    return list(db.query(Attempt).filter_by(round_item_id=item_id).order_by(Attempt.id))


def test_answer_again_creates_linked_immediate_attempt(client, world, db):
    use_scripted(client)
    rid = start_practice(client, world)
    original = answer_ready(client, rid, factual=35)
    before = state(client, rid)
    assert (before["phase"], before["part"]) == ("feedback", "main")
    assert before["can_answer_again"] is True

    after = answer_again(client, rid, original)
    assert (after["phase"], after["part"], after["current_index"]) == ("question", "main", 0)
    current = after["current_attempt"]
    assert current["attempt_kind"] == "immediate"
    assert current["parent_attempt_id"] == original
    assert current["outcome"] == "pending"
    assert after["current"]["item_id"] == before["current"]["item_id"]
    assert after["can_answer_again"] is False
    assert answer_keys_in(after) == set()  # no rubric or model answer while recording
    item_id = before["current"]["item_id"]
    assert [a.attempt_kind for a in attempts_of(db, item_id)] == ["initial", "immediate"]


def test_answer_again_only_once(client, world, db):
    use_scripted(client)
    rid = start_practice(client, world)
    original = answer_ready(client, rid, factual=35)
    answer_again(client, rid, original)
    # paused mid-recording and resumed: the restart is still a repeat
    act(client, rid, "question_ready", "question")
    act(client, rid, "start_answer", "preparation", start_reason="auto")
    act(client, rid, "pause", "recording")
    resumed = act(client, rid, "resume", "recording")
    assert resumed["current_attempt"]["attempt_kind"] == "immediate"
    assert resumed["current_attempt"]["parent_attempt_id"] == original
    assert resumed["can_answer_again"] is False

    repeat = answer_ready(client, rid, factual=70)
    done = state(client, rid)
    assert done["phase"] == "feedback"
    assert done["can_answer_again"] is False
    response = client.post(
        f"/api/rounds/{rid}/actions",
        json={"action": "answer_again", "from_phase": "feedback", "attempt_id": repeat},
    )
    assert response.status_code == 409
    item_id = done["current"]["item_id"]
    kinds = [(a.attempt_kind, a.outcome) for a in attempts_of(db, item_id)]
    assert kinds == [
        ("initial", "answered"),
        ("immediate", "interrupted"),
        ("immediate", "answered"),
    ]


def test_answer_again_offer_rules(client, world, db):
    use_scripted(client)
    rid = start_practice(client, world, count=3)
    act(client, rid, "question_ready", "question")
    act(client, rid, "dont_know", "preparation")
    assert state(client, rid)["can_answer_again"] is True
    act(client, rid, "next", "feedback")
    act(client, rid, "skip", "question")
    assert state(client, rid)["can_answer_again"] is True
    act(client, rid, "next", "feedback")
    act(client, rid, "question_ready", "question")
    failed = act(client, rid, "mic_failure", "preparation")
    assert failed["phase"] == "feedback"
    assert failed["can_answer_again"] is False  # its recovery is "Try again"
    response = client.post(
        f"/api/rounds/{rid}/actions", json={"action": "answer_again", "from_phase": "feedback"}
    )
    assert response.status_code == 409

    off = start_practice(client, world, count=1, immediate_repeat=False)
    answer_ready(client, off)
    assert state(client, off)["can_answer_again"] is False

    exam = client.post(
        "/api/rounds",
        json={
            "subject_id": world.subject.id,
            "config": {
                "scope": {"kind": "subject"},
                "feedback_timing": "exam",
                "question_count": 1,
                "immediate_repeat": True,
            },
        },
    )
    assert exam.status_code == 422  # an exam round with Answer again cannot exist

    twice = start_practice(client, world, count=1)
    original = answer_ready(client, twice)
    first = answer_again(client, twice, original)
    second = answer_again(client, twice, original)  # the same click sent again
    assert second["current_attempt"]["id"] == first["current_attempt"]["id"]
    item_id = first["current"]["item_id"]
    assert [a.attempt_kind for a in attempts_of(db, item_id)] == ["initial", "immediate"]


def test_repeat_after_follow_up(client, world, db):
    use_scripted(client)
    rid = start_practice(client, world, count=1, follow_ups={"enabled": True})
    original = answer_ready(client, rid, factual=35)
    asked = state(client, rid)
    assert (asked["phase"], asked["part"]) == ("question", "followup")
    follow = answer_current(client, rid)
    post_answer(client, follow)
    feedback = state(client, rid)
    assert (feedback["phase"], feedback["part"]) == ("feedback", "main")
    assert feedback["can_answer_again"] is True

    answer_again(client, rid, original)
    repeat = answer_current(client, rid)
    processing = state(client, rid)
    assert (processing["phase"], processing["part"]) == ("processing", "main")
    attempt = db.get(Attempt, repeat)
    assert rounds.disclosure_pending(db, attempt) is False
    post_answer(client, repeat)
    shown = state(client, rid)
    assert (shown["phase"], shown["part"]) == ("feedback", "main")
    assert shown["current_attempt"]["id"] == repeat
    assert get(client, f"/attempts/{repeat}/result")["withheld"] is False
    db.expire_all()
    assert db.query(RoundItem).filter_by(round_id=rid, role="followup").count() == 1


def _without_again(report: dict) -> dict:
    report = dict(report)
    report["items"] = [{k: v for k, v in i.items() if k != "again"} for i in report["items"]]
    for key in ("repeats", "pause_count", "paused_ms"):
        report.pop(key, None)
    return report


def test_repeat_never_changes_primary_score(client, world, db):
    use_scripted(client)
    rid = start_practice(client, world, count=2)
    original = answer_ready(client, rid, factual=35)
    report_before = get(client, f"/reports/rounds/{rid}")
    history_before = get(client, "/reports/history")
    summary_before = get(client, f"/rounds/{rid}/summary")

    answer_again(client, rid, original)
    # an interrupted repeat never marks the original as restarted
    act(client, rid, "question_ready", "question")
    act(client, rid, "pause", "preparation")
    act(client, rid, "resume", "preparation")
    pending = answer_current(client, rid)  # the repeat, still processing
    still = get(client, f"/reports/rounds/{rid}")
    assert still["provisional"] is False
    assert _without_again(still) == _without_again(report_before)
    client.scripted.queue.append(88.75)
    post_answer(client, pending)

    report_after = get(client, f"/reports/rounds/{rid}")
    assert _without_again(report_after) == _without_again(report_before)
    assert report_after["restarted_ordinals"] == []
    assert get(client, "/reports/history") == history_before
    summary_after = get(client, f"/rounds/{rid}/summary")
    assert summary_after["outcomes"] == summary_before["outcomes"]
    assert summary_after["interrupted_attempt_ids"] == []
    assert summary_after["unresolved_attempt_ids"] == []


def coverage_line(client, world) -> dict:
    body = get(client, f"/coverage/subjects/{world.subject.id}")
    lines = [line for topic in body["topics"] for line in topic["objectives"] if line["answers"]]
    assert len(lines) == 1
    return lines[0]


def test_repeat_is_assisted_coverage_evidence(client, world, db):
    use_scripted(client)
    rid = start_practice(client, world, count=2)
    original = answer_ready(client, rid, factual=25)
    answer_again(client, rid, original)
    answer_ready(client, rid, factual=70)
    line = coverage_line(client, world)
    assert (line["status"], line["assisted"]) == ("improving", True)  # assisted success
    assert (line["answers"], line["independent_answers"]) == (2, 1)


def test_skipped_repeat_adds_no_evidence(client, world, db):
    use_scripted(client)
    rid = start_practice(client, world, count=2)
    original = answer_ready(client, rid, factual=25)
    answer_again(client, rid, original)
    act(client, rid, "skip", "question")
    line = coverage_line(client, world)
    assert (line["status"], line["assisted"], line["answers"]) == ("needs_work", False, 1)


def test_abandon_during_repeat_keeps_item_done(client, world, db):
    """An answered item stays done when its Answer again repeat is cut off by an abandon."""
    use_scripted(client)
    rid = start_practice(client, world)
    original = answer_ready(client, rid, factual=35)
    answer_again(client, rid, original)
    act(client, rid, "question_ready", "question")
    act(client, rid, "start_answer", "preparation", start_reason="auto")
    ended = act(client, rid, "abandon", "recording")
    assert ended["status"] == "abandoned"
    item = ended["items"][0]
    assert item["status"] == "done"
    assert item["latest_attempt"]["id"] == original
    assert item["latest_attempt"]["attempt_kind"] == "initial"
