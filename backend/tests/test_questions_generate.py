import json

import pytest
from test_questions_support import QuestionFaker, all_good
from test_topics_support import make_question, make_subject, make_topic, make_version, upgrade

from app.db.session import SessionLocal
from app.features.providers.fakes import FakeTextGenerator
from app.features.providers.registry import _generation_dep
from app.features.providers.types import ProviderError
from app.features.questions.models import QuestionFamily, QuestionVersion, RubricVersion
from app.features.questions.prompts import Verification, VerificationItem
from app.features.questions.schemas import Rubric


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    subject = make_subject(db)
    _, passages = make_version(db, subject.id, count=8, excluded=(7,))
    a = make_topic(db, subject.id, "Ateny", passages[:4], ("Wyjaśnić demokrację",), (), 0)
    b = make_topic(db, subject.id, "Sparta", passages[4:], ("Opisać Spartę",), (), 1)
    return subject, passages, a, b


@pytest.fixture
def fake():
    return FakeTextGenerator({"questions.generate": QuestionFaker(), "questions.verify": all_good})


@pytest.fixture
def api(client, fake):
    client.app.dependency_overrides[_generation_dep] = lambda: fake
    return client


def generate(api, subject_id, **body):
    payload = {"subject_id": subject_id, "per_topic": 2, "style": "open", "difficulty": "recall"}
    return api.post("/api/questions/generate", json={**payload, **body})


def calls(fake, task="questions.generate"):
    return [c for c in fake.calls if c["task"] == task]


def test_batches_of_at_most_5_and_8000_tokens(db, api, fake, world):
    subject, _, a, _ = world
    response = generate(api, subject.id, topic_ids=[a.id], per_topic=7)
    assert response.status_code == 202, response.text
    generation = calls(fake)
    assert len(generation) == 2
    assert [c["messages"][0].content.count("Question ") for c in generation] == [5, 2]
    assert all(c["max_output_tokens"] == 8000 for c in fake.calls)
    assert len(calls(fake, "questions.verify")) == 2
    assert db.query(QuestionVersion).count() == 7


def test_prompt_contains_existing_questions_and_passages_tagged(db, api, fake, world):
    subject, passages, a, _ = world
    make_question(db, subject.id, a.id, text="Czym była eklezja w Atenach?")
    generate(api, subject.id, topic_ids=[a.id], per_topic=1)
    call = calls(fake)[0]
    content = call["messages"][0].content
    for passage in passages[:4]:
        assert f'<passage id="{passage.id}">' in content
    assert "Czym była eklezja w Atenach?" in content
    assert "Wyjaśnić demokrację" in content
    system = call["system"]
    assert "Polish" in system and "ignore" in system.lower()
    assert "Omów" in system  # open-style phrasing


def test_excluded_passages_not_sent(db, api, fake, world):
    subject, passages, _, b = world
    generate(api, subject.id, topic_ids=[b.id], per_topic=1)
    content = calls(fake)[0]["messages"][0].content
    assert f'<passage id="{passages[7].id}">' not in content
    assert f'<passage id="{passages[4].id}">' in content


def test_mixed_style_alternates(db, api, fake, world):
    subject, _, a, _ = world
    generate(api, subject.id, topic_ids=[a.id], per_topic=7, style="mixed", difficulty="mixed")
    contents = [c["messages"][0].content for c in calls(fake)]
    assert "Question 1: style=short, difficulty=recall" in contents[0]
    assert "Question 2: style=open, difficulty=apply" in contents[0]
    assert "Question 3: style=short, difficulty=analysis" in contents[0]
    # the second batch continues the sequence (question index 5 → short, apply... index 6 → open)
    assert "Question 1: style=open, difficulty=analysis" in contents[1]
    assert "Question 2: style=short, difficulty=recall" in contents[1]
    styles = [q.style for q in db.query(QuestionVersion).order_by(QuestionVersion.id)]
    assert styles == ["short", "open", "short", "open", "short", "open", "short"]


def test_saved_with_rubric_and_provenance(db, api, fake, world):
    subject, passages, a, _ = world
    generate(api, subject.id, topic_ids=[a.id], per_topic=1)
    question = db.query(QuestionVersion).one()
    family = db.get(QuestionFamily, question.family_id)
    assert family.primary_topic_id == a.id and family.subject_id == subject.id
    assert question.version == 1 and question.status == "active"
    assert question.source_policy == "materials"
    assert question.objective_id is not None
    rubric = Rubric.model_validate_json(
        db.query(RubricVersion).filter_by(question_version_id=question.id).one().rubric_json
    )
    assert [p.passage_ids for p in rubric.essential_points] == [
        [passages[0].id, passages[1].id]
    ] * 3
    assert rubric.model_answer == "Zwięzła odpowiedź modelowa."
    provenance = json.loads(question.provenance_json)
    assert provenance["prompt_version"] == "questions-v4"
    assert provenance["provider"] == "openai"
    assert provenance["model"] == "fake"
    assert provenance["operation_id"].startswith("questions.generate:")
    assert provenance["served_by_fallback"] is False
    assert provenance["origin"] == "ai"


def test_verification_flags_needs_review(db, api, fake, world):
    subject, _, a, _ = world

    def doubtful(messages):
        return Verification(
            items=[
                VerificationItem(index=0, answerable=True, grounded=True, note=""),
                VerificationItem(index=1, answerable=True, grounded=False, note="Brak w źródle."),
            ]
        )

    fake.responses["questions.verify"] = doubtful
    job_id = generate(api, subject.id, topic_ids=[a.id], per_topic=2).json()["job_id"]
    statuses = [
        (q.status, q.review_note) for q in db.query(QuestionVersion).order_by(QuestionVersion.id)
    ]
    assert statuses == [("active", None), ("needs_review", "Brak w źródle.")]
    summary = api.get(f"/api/jobs/{job_id}").json()["summary"]
    assert summary["saved"] == 2 and summary["needs_review"] == 1


def test_no_topics_409(db, api, world):
    other = make_subject(db, "Biologia")
    db.commit()
    response = generate(api, other.id)
    assert response.status_code == 409
    assert response.json()["detail"] == "Analyze the materials first to identify the topics."
    assert generate(api, 99999).status_code == 404
    assert generate(api, other.id, per_topic=11).status_code == 422


def test_provider_failure_mid_job_resume(db, api, fake, world):
    subject, _, a, b = world
    faker = QuestionFaker()
    count = {"n": 0}

    def flaky(messages):
        count["n"] += 1
        if count["n"] == 2:
            raise ProviderError("rate_limit", "openai", "Provider rate limit exceeded.")
        return faker(messages)

    fake.responses["questions.generate"] = flaky
    job_id = generate(api, subject.id, per_topic=2).json()["job_id"]
    job = api.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert job["steps_done"] == 1 and job["steps_total"] == 2
    assert db.query(QuestionVersion).count() == 2
    assert api.post(f"/api/jobs/{job_id}/resume").status_code == 202
    assert count["n"] == 3  # batch 1 was not regenerated
    db.expire_all()
    assert db.query(QuestionVersion).count() == 4
    topics = {
        db.get(QuestionFamily, q.family_id).primary_topic_id for q in db.query(QuestionVersion)
    }
    assert topics == {a.id, b.id}
    assert api.get(f"/api/jobs/{job_id}").json()["status"] == "done"
