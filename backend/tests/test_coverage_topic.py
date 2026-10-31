from datetime import timedelta

import pytest
from test_attempts_audio_wait import age
from test_attempts_support import FakeProviders, answered_attempt, override_providers
from test_coverage_queries import WRITES, reveal, statements
from test_coverage_subject import ANCHOR, Scene, day
from test_coverage_support import make_answer, make_objective_question
from test_rounds_support import make_world
from test_topics_support import make_subject, make_topic, make_version, upgrade

from app.db.session import SessionLocal
from app.features.attempts.models import AttemptProcessing
from app.features.questions.models import QuestionFamily, QuestionVersion
from app.features.rounds.models import Attempt, RoundItem
from app.features.topics.models import Term, Topic


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def topic_detail(client, topic_id) -> dict:
    response = client.get(f"/api/coverage/topics/{topic_id}")
    assert response.status_code == 200, response.text
    return response.json()


def test_topic_detail(client, db):
    scene = Scene(db, (("Temat A", ("O1", "O2")), ("Temat B", ("O3",))))
    topic = scene.topics["Temat A"]
    topic.description = "Opis tematu."
    make_topic(db, scene.subject.id, "Obcy", ordinal=9, terms=("ostracyzm",))
    db.add_all(
        [
            Term(subject_id=scene.subject.id, topic_id=topic.id, term=t, term_key=t.casefold())
            for t in ("polis", "archont")
        ]
    )
    db.commit()
    scene.answer("O1", day(3), "needs_improvement", 51)
    scene.answer("O2", day(2), "good", 66)
    body = topic_detail(client, topic.id)
    assert body["title"] == "Temat A" and body["description"] == "Opis tematu."
    assert body["subject_id"] == scene.subject.id and body["subject_name"] == "Historia"
    assert body["status"] == "needs_work"
    assert body["question_count"] == 2
    assert body["terms"] == ["archont", "polis"]
    assert body["passage_count"] == 2
    subject_lines = scene.coverage(client)["topics"][0]["objectives"]
    assert body["objectives"] == subject_lines
    other = topic_detail(client, scene.topics["Temat B"].id)
    assert other["status"] == "not_practised" and other["history"] == []


def test_history_rows(client, db):
    scene = Scene(db, (("Temat A", ("O1", "O2")),))
    scene.answer("O1", day(6), "good", 66, prep_ms=5000)
    scene.answer("O1", day(5), None, processing="failed")
    scene.answer("O1", day(4), None, processing="processing")
    scene.answer("O2", day(3), None, outcome="interrupted")
    scene.answer("O2", day(2), None, outcome="dont_know")
    scene.answer("O2", day(1), "very_good", 88, kind="immediate")
    exam = scene.round(day(0), timing="exam", status="active", current_index=5)
    scene.answer("O2", day(0), "very_good", round_=exam)
    rows = topic_detail(client, scene.topics["Temat A"].id)["history"]
    assert [r["status"] for r in rows] == [
        "ready",
        "ready",
        "interrupted",
        "processing",
        "failed",
        "ready",
    ]
    first, dont_know, interrupted, processing, failed, oldest = rows
    assert first["attempt_kind"] == "immediate" and first["independent"] is False
    assert first["band"] == "very_good" and first["factual"] == 88 and first["language"] == 70
    assert first["difficulty"] == "recall" and first["question_text"] == "Pytanie o O2?"
    assert first["preparation_used_ms"] == 8000
    assert dont_know["outcome"] == "dont_know" and dont_know["band"] == "weak"
    assert dont_know["factual"] == 0 and dont_know["language"] is None
    assert failed["band"] is None and failed["factual"] is None
    assert processing["band"] is None and interrupted["band"] is None
    assert oldest["independent"] is True and oldest["band"] == "good"
    assert oldest["preparation_used_ms"] == 5000
    assert oldest["objective_id"] == scene.objectives["O1"].id
    assert oldest["at"] < first["at"]


def test_history_matches_the_lines_for_objective_less_questions(client, db):
    scene = Scene(db, (("Temat A", ("O1",)),))
    topic = scene.topics["Temat A"]
    for status in ("active", "needs_review", "followup", "retired"):
        question = make_objective_question(
            db, scene.subject.id, topic.id, None, f"Pytanie {status}?", status=status
        )
        make_answer(db, scene.round(day(2)), question, at=day(2), band="good")
    scene.answer("O1", day(1), "good")
    body = topic_detail(client, topic.id)
    shown = sorted(r["question_text"] for r in body["history"])
    assert shown == ["Pytanie active?", "Pytanie needs_review?", "Pytanie o O1?"]
    whole = [line for line in body["objectives"] if line["objective_id"] is None]
    assert [line["answers"] for line in whole] == [2]


def test_history_limit(client, db):
    scene = Scene(db, (("Temat A", ("O1",)),))
    round_ = scene.round(day(3))
    for n in range(55):
        scene.answer("O1", day(3) + timedelta(minutes=n), "good", round_=round_)
    rows = topic_detail(client, scene.topics["Temat A"].id)["history"]
    assert len(rows) == 50
    assert rows[0]["at"] > rows[-1]["at"]


def test_current_answer_of_a_running_round_is_not_in_history(client, db):
    scene = Scene(db, (("Temat A", ("O1",)),))
    practice = scene.round(day(1), status="active", phase="processing", current_index=0)
    scene.answer("O1", day(1), "good", round_=practice)
    assert topic_detail(client, scene.topics["Temat A"].id)["history"] == []


def test_sources(client, db):
    subject = make_subject(db, "Zrodla")
    _, passages = make_version(db, subject.id, count=25, size=400, excluded=(1,))
    topic = make_topic(db, subject.id, "Ateny", passages[:3])
    body = topic_detail(client, topic.id)
    sources = body["sources"]
    assert [s["passage_id"] for s in sources] == [passages[0].id, passages[2].id]
    assert body["passage_count"] == 2
    first = sources[0]
    assert first["document_version"] == 1 and first["locator"] == "sekcja 1"
    assert first["document_title"] == "Podręcznik" and first["heading"] == "Rozdział 1"
    assert len(first["excerpt"]) <= 240 and first["excerpt"].endswith("…")
    wide = make_topic(db, subject.id, "Wszystko", passages, ordinal=2)
    body = topic_detail(client, wide.id)
    assert len(body["sources"]) == 20
    assert body["passage_count"] == 24


def test_removed_or_unknown_topic_404(client, db):
    scene = Scene(db, (("Temat A", ("O1",)),))
    topic = scene.topics["Temat A"]
    response = client.get("/api/coverage/topics/999")
    assert response.status_code == 404 and response.json()["detail"] == "Topic not found."
    db.get(Topic, topic.id).status = "removed"
    db.commit()
    response = client.get(f"/api/coverage/topics/{topic.id}")
    assert response.status_code == 404 and response.json()["detail"] == "Topic not found."


def test_topic_query_budget_and_read_only(client, db):
    scene = Scene(db, (("Temat A", ("O1", "O2")),))
    topic_id = scene.topics["Temat A"].id

    def get():
        assert client.get(f"/api/coverage/topics/{topic_id}").status_code == 200

    scene.answer("O1", day(1), "good")
    small = statements(get)
    for n in range(6):
        at = ANCHOR - timedelta(days=n, hours=1)
        round_ = scene.round(at)
        for text in ("O1", "O2", "O1"):
            scene.answer(text, at, "good", round_=round_)
    large = statements(get)
    assert len(small) == len(large) <= 14, (len(small), len(large))
    assert [s for s in large if s.startswith(WRITES)] == []


def test_topic_detail_does_not_settle_missing_audio(client, db):
    override_providers(client.app, FakeProviders())
    world = make_world(db)
    round_id, attempt = answered_attempt(client, world)
    age(db, attempt, 200)
    reveal(db, round_id)
    item = db.get(RoundItem, db.get(Attempt, attempt).round_item_id)
    family_id = db.get(QuestionVersion, item.question_version_id).family_id
    topic_id = db.get(QuestionFamily, family_id).primary_topic_id
    for _ in range(2):
        seen = statements(lambda: client.get(f"/api/coverage/topics/{topic_id}"))
        assert [s for s in seen if s.startswith(WRITES)] == []
    history = topic_detail(client, topic_id)["history"]
    assert [(h["attempt_id"], h["status"]) for h in history] == [(attempt, "processing")]
    db.expire_all()
    assert db.query(AttemptProcessing).count() == 0
    stored = db.get(Attempt, attempt)
    assert (stored.outcome, stored.processing_status) == ("answered", "processing")
