import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from test_attempts_audio_wait import age
from test_attempts_support import (
    FakeProviders,
    act,
    answer_current,
    answered_attempt,
    override_providers,
    post_answer,
    start_round,
)
from test_rounds_support import make_world
from test_topics_support import make_question, make_subject, make_topic, make_version, upgrade

from app.db.session import SessionLocal
from app.features.attempts.models import AttemptProcessing
from app.features.rounds.models import Attempt


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    return make_world(db)


def second_subject(db) -> dict:
    subject = make_subject(db, "Geografia")
    _, passages = make_version(db, subject.id, count=2, excluded=())
    topic = make_topic(db, subject.id, "Rzeki", passages)
    for n in range(2):
        make_question(
            db, subject.id, topic.id, f"Pytanie o rzekach {n}.", passage_ids=(passages[0].id,)
        )
    return {"subject": subject}


def history(client, **params) -> list[dict]:
    response = client.get("/api/reports/history", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def finished_round(client, world, timing="practice") -> int:
    rid = start_round(client, world, timing, count=1)["id"]
    attempt = answer_current(client, rid)
    post_answer(client, attempt)
    if timing == "practice":
        act(client, rid, "next", "feedback")
    return rid


def test_history_newest_first_filters_and_limit(client, world, db):
    override_providers(client.app, FakeProviders())
    other = second_subject(db)
    first = finished_round(client, world)
    exam = finished_round(client, world, "exam")
    geo = finished_round(client, other)
    rows = history(client)
    assert [r["round_id"] for r in rows] == [geo, exam, first]
    assert [r["subject_name"] for r in rows] == ["Geografia", "Historia", "Historia"]
    assert history(client, subject_id=world["subject"].id)[0]["round_id"] == exam
    assert [r["round_id"] for r in history(client, subject_id=other["subject"].id)] == [geo]
    assert [r["round_id"] for r in history(client, feedback_timing="exam")] == [exam]
    assert [r["round_id"] for r in history(client, feedback_timing="practice")] == [geo, first]

    done = next(r for r in rows if r["round_id"] == first)
    assert done["status"] == "finished" and done["feedback_timing"] == "practice"
    assert (done["asked"], done["total"]) == (1, 1)
    assert done["primary_score"] is not None and done["withheld"] is False
    assert sum(done["band_distribution"].values()) == 1
    assert done["median_preparation_used_ms"] is not None

    for _ in range(50):
        start_round(client, world, count=1)
    assert len(history(client)) == 50


def test_withheld_exam_round_has_no_score(client, world):
    override_providers(client.app, FakeProviders())
    rid = start_round(client, world, "exam", count=2)["id"]
    post_answer(client, answer_current(client, rid))
    (row,) = history(client)
    assert row["round_id"] == rid and row["withheld"] is True and row["status"] == "active"
    assert row["primary_score"] is None
    assert row["band_distribution"] == {
        "weak": 0,
        "needs_improvement": 0,
        "good": 0,
        "very_good": 0,
    }
    assert (row["asked"], row["total"]) == (1, 2)


def test_unknown_feedback_timing_is_422(client, world):
    response = client.get("/api/reports/history", params={"feedback_timing": "quiz"})
    assert response.status_code == 422


def count_statements(client, call) -> int:
    seen = []

    def listener(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", listener)
    try:
        call()
    finally:
        event.remove(Engine, "before_cursor_execute", listener)
    return len(seen)


def test_history_query_count_does_not_grow_with_items(client, world, db):
    override_providers(client.app, FakeProviders())
    athens = world["topics"][0]
    for n in range(2):
        make_question(db, world["subject"].id, athens.id, f"Pytanie dodatkowe {n}.")
    for _ in range(5):
        rid = start_round(client, world, count=10)["id"]
        post_answer(client, answer_current(client, rid))
        act(client, rid, "next", "feedback")
        for _ in range(9):
            act(client, rid, "question_ready", "question")
            act(client, rid, "dont_know", "preparation")
            act(client, rid, "next", "feedback")
    rows = history(client)
    assert len(rows) == 5 and all((r["asked"], r["total"]) == (10, 10) for r in rows)

    statements = count_statements(client, lambda: history(client))
    assert statements <= 60, statements  # reading each result through get_result took ~250


def test_history_does_not_settle_missing_audio(client, world, db):
    rid, attempt = answered_attempt(client, world)
    age(db, attempt, 200)
    (row,) = history(client)
    assert row["provisional"] is True and row["primary_score"] is None
    db.expire_all()
    assert db.query(AttemptProcessing).count() == 0
    stored = db.get(Attempt, attempt)
    assert (stored.outcome, stored.processing_status) == ("answered", "processing")

    report = client.get(f"/api/reports/rounds/{rid}").json()  # the round report still settles
    assert report["items"][0]["failure"]["kind"] == "audio_missing"
    db.expire_all()
    assert db.get(Attempt, attempt).outcome == "technical_failure"
