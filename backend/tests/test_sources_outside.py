"""Generation "Only expand" (stored `outside`): questions beyond the materials, no citations."""

import pytest
from sqlalchemy import select
from test_attempts_support import (
    FOLLOW_UP_TASK,
    FakeProviders,
    answer_current,
    follow_up_for,
    override_providers,
    post_answer,
    start_round,
)
from test_chapters_support import add_job
from test_questions_support import QuestionFaker, all_good
from test_rounds_support import make_world
from test_topics_support import make_subject, make_topic, make_version, passage_ids_in, upgrade

from app.db.session import SessionLocal
from app.features.assessment.prompts import assessment_system
from app.features.follow_ups import prompts as follow_up_prompts
from app.features.follow_ups.models import FollowUp
from app.features.languages.profiles import PROFILES
from app.features.providers.fakes import FakeTextGenerator, FakeTranscriber
from app.features.providers.registry import _generation_dep
from app.features.questions import prompts, validation
from app.features.questions.models import QuestionVersion, RubricVersion
from app.features.questions.prompts import Verification, VerificationItem
from app.features.questions.schemas import EnrichmentPoint, Rubric, SourceIssue
from app.features.rounds.models import RoundItem

NOT_OUTSIDE = "point taken from the materials"
OTHER_POLICY = (
    "A question batch with a different source policy is already running. Wait for it to finish."
)


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    subject = make_subject(db)
    _, passages = make_version(db, subject.id, count=6, excluded=())
    topic = make_topic(db, subject.id, "Ateny", passages[:4], ("Wyjaśnić demokrację",), (), 0)
    return subject, passages, topic


class OutsideFaker(QuestionFaker):
    """Every point expanded, but with passage ids everywhere (which must be cleared)."""

    def __call__(self, messages):
        batch = super().__call__(messages)
        for q in batch.questions:
            ids = q.rubric.essential_points[0].passage_ids
            for point in q.rubric.essential_points:
                point.basis = "expanded"
                point.passage_ids = list(ids)
            q.rubric.enrichment_points = [EnrichmentPoint(text="Dodatkowo", passage_ids=ids)]
            q.rubric.source_issues = [SourceIssue(kind="unclear", passage_ids=ids, note="Uwaga")]
            q.rubric.beyond_material = ["Porównanie z Rzymem."]
        return batch


class CleanOutsideFaker(OutsideFaker):
    """Outside questions without source issues (an issue alone sends a question to review)."""

    def __call__(self, messages):
        batch = super().__call__(messages)
        for q in batch.questions:
            q.rubric.source_issues = []
        return batch


class SourcePointFaker(OutsideFaker):
    def __init__(self, basis: str) -> None:
        super().__init__()
        self.basis = basis

    def __call__(self, messages):
        batch = super().__call__(messages)
        for q in batch.questions:
            q.rubric.essential_points[0].basis = self.basis
        return batch


def generate(client, subject_id, faker, verify=all_good, **body):
    fake = FakeTextGenerator({"questions.generate": faker, "questions.verify": verify})
    client.app.dependency_overrides[_generation_dep] = lambda: fake
    payload = {"subject_id": subject_id, "per_topic": 1, "source_policy": "outside", **body}
    response = client.post("/api/questions/generate", json=payload)
    return fake, response


def rubric_of(db, version_id: int) -> Rubric:
    row = db.scalar(select(RubricVersion).where(RubricVersion.question_version_id == version_id))
    return Rubric.model_validate_json(row.rubric_json)


def test_outside_generation_prompt(db, client, world):
    subject, passages, topic = world
    from test_topics_support import make_question

    make_question(db, subject.id, topic.id, "Istniejące pytanie o Ateny.")
    fake, response = generate(client, subject.id, OutsideFaker())
    assert response.status_code == 202, response.text
    call = next(c for c in fake.calls if c["task"] == "questions.generate")
    assert call["system"] == prompts.generate_system("outside", profile=PROFILES["pl"])
    assert "„Tylko poszerzenie”" in call["system"] and "beyond" in call["system"]
    assert call["system"] != prompts.generate_system("expanded", profile=PROFILES["pl"])
    message = call["messages"][0].content
    assert "Source policy: Tylko poszerzenie" in message
    assert set(passage_ids_in(call["messages"])) == {p.id for p in passages[:4]}
    assert "Istniejące pytanie o Ateny." in message
    verify = next(c for c in fake.calls if c["task"] == "questions.verify")
    assert verify["system"] == prompts.verify_system("outside", profile=PROFILES["pl"])
    assert "basis: expanded" in verify["messages"][0].content
    assert prompts.POLICY_NAMES["outside"] == "Tylko poszerzenie"
    assert prompts.QUESTIONS_PROMPT_VERSION == "questions-v4"


def test_outside_validation(db, client, world):
    subject, _, _ = world
    _, response = generate(client, subject.id, OutsideFaker())
    assert response.status_code == 202, response.text
    version = db.scalars(select(QuestionVersion)).one()
    assert version.source_policy == "outside"
    rubric = rubric_of(db, version.id)
    assert {p.basis for p in rubric.essential_points} == {"expanded"}
    assert all(p.passage_ids == [] for p in rubric.essential_points)
    assert all(e.passage_ids == [] for e in rubric.enrichment_points)
    assert all(i.passage_ids == [] for i in rubric.source_issues)
    assert rubric.beyond_material == ["Porównanie z Rzymem."]
    assert validation.question_basis(rubric, "outside") == "expanded"
    detail = client.get(f"/api/questions/{version.id}").json()
    assert detail["basis"] == "expanded" and detail["passages"] == []

    for basis in ("source", "mixed"):
        _, response = generate(client, subject.id, SourcePointFaker(basis))
        summary = client.get(f"/api/jobs/{response.json()['job_id']}").json()["summary"]
        assert summary["rejected"] == {NOT_OUTSIDE: 1} and summary["saved"] == 0


def test_outside_verification_reuses_review_notes(db, client, world):
    subject, _, _ = world

    def doubtful(messages):
        return Verification(
            items=[VerificationItem(index=0, answerable=True, grounded=False, note="Wątpliwe.")]
        )

    generate(client, subject.id, CleanOutsideFaker())
    other = CleanOutsideFaker(["Jak demokracja ateńska wpłynęła na Rzym?"])
    generate(client, subject.id, other, doubtful)
    statuses = [
        (v.status, v.review_note)
        for v in db.scalars(select(QuestionVersion).order_by(QuestionVersion.id))
    ]
    assert statuses == [("active", None), ("needs_review", "Wątpliwe.")]


def test_outside_edit_keeps_policy(db, client, world):
    subject, passages, _ = world
    generate(client, subject.id, OutsideFaker())
    version = db.scalars(select(QuestionVersion)).one()
    rubric = rubric_of(db, version.id).model_dump()
    rubric["essential_points"][0]["text"] = "Nowe sformułowanie"
    edited = client.put(f"/api/questions/{version.id}", json={"rubric": rubric})
    assert edited.status_code == 200, edited.text
    assert edited.json()["source_policy"] == "outside"
    rubric["essential_points"][0]["basis"] = "source"
    rubric["essential_points"][0]["passage_ids"] = [passages[0].id]
    refused = client.put(f"/api/questions/{edited.json()['version_id']}", json={"rubric": rubric})
    assert refused.status_code == 422
    assert NOT_OUTSIDE in refused.json()["detail"]


def test_outside_assessment_uses_expanded_paragraph():
    assert assessment_system("outside", profile=PROFILES["pl"]) == assessment_system(
        "expanded", profile=PROFILES["pl"]
    )


def outside_follow_up(messages):
    generated = follow_up_for(passage_ids_in(messages))
    for point in generated.rubric.essential_points:
        point.basis = "expanded"
    return generated


def source_follow_up(messages):
    return follow_up_for(passage_ids_in(messages))


def run_follow_up(client, db, generator):
    world = make_world(db)
    fake = FakeProviders(transcriber=FakeTranscriber("Ostracyzm to wygnanie."), follow_up=generator)
    override_providers(client.app, fake)
    rid = start_round(client, world, "practice", 1, follow_ups={"enabled": True})["id"]
    item = db.scalar(select(RoundItem).where(RoundItem.round_id == rid))
    db.get(QuestionVersion, item.question_version_id).source_policy = "outside"
    db.commit()
    post_answer(client, answer_current(client, rid))
    db.expire_all()
    return db.scalar(select(FollowUp))


def test_outside_follow_up_inherits_policy(db, client):
    generator = FakeTextGenerator({FOLLOW_UP_TASK: outside_follow_up})
    row = run_follow_up(client, db, generator)
    assert row.status == "ready", row.failure_message
    system = generator.calls[0]["system"]
    assert system == follow_up_prompts.follow_up_system("outside", profile=PROFILES["pl"])
    assert "- Source policy „Tylko poszerzenie” (the same as the main question's)" in system
    item = db.get(RoundItem, row.item_id)
    version = db.get(QuestionVersion, item.question_version_id)
    assert version.source_policy == "outside"
    rubric = rubric_of(db, version.id)
    assert all(p.basis == "expanded" and p.passage_ids == [] for p in rubric.essential_points)


def test_outside_follow_up_rejects_source_points(db, client):
    generator = FakeTextGenerator({FOLLOW_UP_TASK: source_follow_up})
    row = run_follow_up(client, db, generator)
    assert (row.status, row.failure_kind) == ("failed", "invalid_output")
    assert NOT_OUTSIDE in generator.calls[1]["messages"][-1].content


def test_policy_conflict_three_values(db, client, world):
    subject, _, _ = world
    add_job(db, "questions.generate", subject.id, None, "running", {"source_policy": "outside"})
    _, response = generate(client, subject.id, OutsideFaker(), source_policy="materials")
    assert response.status_code == 409 and response.json()["detail"] == OTHER_POLICY
    _, same = generate(client, subject.id, OutsideFaker())
    assert same.status_code == 202


def test_unknown_generation_policy_still_422(db, client, world):
    subject, _, _ = world
    _, response = generate(client, subject.id, OutsideFaker(), source_policy="web")
    assert response.status_code == 422


def test_question_counts_have_an_outside_key(db, client, world):
    subject, _, topic = world
    generate(client, subject.id, OutsideFaker())
    body = client.get(f"/api/topics?subject_id={subject.id}").json()[0]
    assert body["question_counts"] == {"materials": 0, "expanded": 0, "outside": 1}
    assert list(body["question_counts"]) == ["materials", "expanded", "outside"]
