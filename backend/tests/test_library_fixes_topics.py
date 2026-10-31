"""Generate more for topics without questions: per-policy counts and topic_ids limiting batches."""

import json

import pytest
from sqlalchemy import select
from test_questions_support import QuestionFaker, all_good
from test_topics_support import make_question, make_subject, make_topic, make_version, upgrade

from app.db.session import SessionLocal
from app.features.jobs.models import AiJob
from app.features.providers.fakes import FakeTextGenerator
from app.features.providers.registry import _generation_dep
from app.features.questions.models import QuestionFamily, QuestionVersion


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    subject = make_subject(db)
    _, passages = make_version(db, subject.id, count=9, excluded=())
    topics = [
        make_topic(db, subject.id, title, passages[i * 3 : i * 3 + 3], (f"Cel {title}",), (), i)
        for i, title in enumerate(("Ateny", "Sparta", "Teby"))
    ]
    db.commit()
    return subject, topics


@pytest.fixture
def fake():
    return FakeTextGenerator({"questions.generate": QuestionFaker(), "questions.verify": all_good})


def test_topic_ids_limit_the_batches(db, client, fake, world):
    client.app.dependency_overrides[_generation_dep] = lambda: fake
    subject, (ateny, sparta, teby) = world
    body = {"subject_id": subject.id, "per_topic": 2, "topic_ids": [sparta.id, teby.id]}
    response = client.post("/api/questions/generate", json=body)
    assert response.status_code == 202, response.text
    job = db.get(AiJob, response.json()["job_id"])
    db.refresh(job)
    batches = json.loads(job.params_json)["batches"]
    assert [b["topic_id"] for b in batches] == [sparta.id, teby.id]
    topics = set(db.scalars(select(QuestionFamily.primary_topic_id)))
    assert topics == {sparta.id, teby.id}
    assert ateny.id not in topics


def test_topics_carry_question_counts_per_policy(db, client, world):
    subject, (ateny, sparta, teby) = world
    make_question(db, subject.id, ateny.id)
    make_question(db, subject.id, ateny.id, "Drugie pytanie.", status="needs_review")
    make_question(db, subject.id, ateny.id, "Wycofane.", status="retired")
    make_question(db, subject.id, sparta.id, "Rozszerzone.")
    db.query(QuestionVersion).filter(QuestionVersion.text == "Rozszerzone.").update(
        {"source_policy": "expanded"}
    )
    db.commit()
    body = {t["id"]: t for t in client.get(f"/api/topics?subject_id={subject.id}").json()}
    assert body[ateny.id]["question_counts"] == {"materials": 2, "expanded": 0, "outside": 0}
    assert body[ateny.id]["question_count"] == 2
    assert body[sparta.id]["question_counts"] == {"materials": 0, "expanded": 1, "outside": 0}
    assert body[teby.id]["question_counts"] == {"materials": 0, "expanded": 0, "outside": 0}
    assert list(body[teby.id])[-2] == "question_counts"  # appended last, before chapter_id
