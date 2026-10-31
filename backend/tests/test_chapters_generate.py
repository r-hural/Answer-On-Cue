import json

import pytest
from sqlalchemy import select
from test_chapters_support import add_job, weak_extension_round
from test_questions_support import QuestionFaker, all_good
from test_reports_history import count_statements
from test_topics_support import (
    make_question,
    make_subject,
    make_topic,
    make_version,
    passage_ids_in,
    upgrade,
)

from app.db.session import SessionLocal
from app.features.jobs.models import AiJob
from app.features.library.models import Chapter, Document
from app.features.providers.fakes import FakeTextGenerator
from app.features.providers.registry import _generation_dep
from app.features.questions import service as questions
from app.features.questions.models import QuestionFamily
from app.features.rounds import extension

NO_BOOK_TOPICS = "Analyse this document's topics first."
NOT_IMPORTED = "The document was not imported correctly."
FOREIGN = "This chapter belongs to another subject."
GENERATION_RUNNING = (
    "Questions are already being generated for this subject. Wait for that to finish."
)


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def fake():
    return FakeTextGenerator({"questions.generate": QuestionFaker(), "questions.verify": all_good})


@pytest.fixture
def api(client, fake):
    client.app.dependency_overrides[_generation_dep] = lambda: fake
    return client


def default_chapter(db, subject_id: int) -> int:
    return db.scalars(select(Chapter.id).where(Chapter.subject_id == subject_id)).one()


@pytest.fixture
def books(db):
    """One chapter, book 1 (T1, T2 with questions) and book 2 (T3 new, T1 extended)."""
    subject = make_subject(db, "Historia PL")
    one, p1 = make_version(db, subject.id, count=4, excluded=())
    two, p2 = make_version(db, subject.id, count=4, excluded=())
    db.get(Document, two.document_id).title = "Rewolucja francuska"
    db.commit()
    t1 = make_topic(db, subject.id, "Ateny", [p1[0], p1[1], p2[0]], ("Omówić Ateny",), (), 0)
    t2 = make_topic(db, subject.id, "Sparta", p1[2:], ("Omówić Spartę",), (), 1)
    t3 = make_topic(db, subject.id, "Paryż", p2[1:], ("Omówić Paryż",), (), 2)
    make_question(db, subject.id, t1.id, "Istniejące pytanie o Ateny.", passage_ids=(p1[0].id,))
    make_question(db, subject.id, t2.id, "Istniejące pytanie o Spartę.", passage_ids=(p1[2].id,))
    return {"subject": subject, "one": one, "two": two, "p1": p1, "p2": p2,
            "T1": t1, "T2": t2, "T3": t3}  # fmt: skip


def generate(api, status: int = 202, **body):
    response = api.post("/api/questions/generate", json={"per_topic": 1, **body})
    assert response.status_code == status, response.text
    return response.json()


def test_per_book_generation_covers_only_topics_with_material_from_it(db, api, fake, books):
    w = books
    job_id = generate(api, subject_id=w["subject"].id, document_version_id=w["two"].id)["job_id"]
    calls = [c for c in fake.calls if c["task"] == "questions.generate"]
    assert len(calls) == 2
    book_two = {p.id for p in w["p2"]}
    for call in calls:
        assert set(passage_ids_in(call["messages"])) <= book_two
    assert "Istniejące pytanie o Ateny." in calls[0]["messages"][0].content
    assert "Istniejące pytanie o Spartę." not in "".join(c["messages"][0].content for c in calls)
    job = db.get(AiJob, job_id)
    db.refresh(job)
    assert job.document_version_id == w["two"].id
    assert [b["topic_id"] for b in json.loads(job.params_json)["batches"]] == [
        w["T1"].id,
        w["T3"].id,
    ]
    families = set(db.scalars(select(QuestionFamily.primary_topic_id)))
    assert w["T2"].id in families  # only its old question
    listed = api.get(f"/api/subjects/{w['subject'].id}/documents").json()
    flags = {
        d["latest_version"]["id"]: d["latest_version"]["questions_generated_at"] for d in listed
    }
    assert flags[w["two"].id] is not None and flags[w["one"].id] is None


def test_per_book_validation_accepts_only_that_book(db, client, books):
    w = books
    foreign = w["p1"][0].id

    def cite_book_one(messages):
        batch = QuestionFaker()(messages)
        for question in batch.questions:
            for point in question.rubric.essential_points:
                point.passage_ids = [foreign]
        return batch

    fake = FakeTextGenerator({"questions.generate": cite_book_one, "questions.verify": all_good})
    client.app.dependency_overrides[_generation_dep] = lambda: fake
    job_id = generate(
        client, subject_id=w["subject"].id, document_version_id=w["two"].id,
        topic_ids=[w["T1"].id],
    )["job_id"]  # fmt: skip
    summary = client.get(f"/api/jobs/{job_id}").json()["summary"]
    assert summary["saved"] == 0
    assert summary["rejected"] == {"no source for a point": 1}


def test_book_without_topics_409(db, api, books):
    w = books
    lonely, _ = make_version(db, w["subject"].id, count=2, excluded=())
    response = generate(api, 409, subject_id=w["subject"].id, document_version_id=lonely.id)
    assert response["detail"] == NO_BOOK_TOPICS
    failed, _ = make_version(db, w["subject"].id, count=2, excluded=(), status="failed")
    response = generate(api, 409, subject_id=w["subject"].id, document_version_id=failed.id)
    assert response["detail"] == NOT_IMPORTED


def test_chapter_generation(db, api, fake, books):
    w = books
    chapter = Chapter(subject_id=w["subject"].id, name="Rewolucja", is_default=False, ordinal=1)
    db.add(chapter)
    db.commit()
    w["T3"].chapter_id = chapter.id
    db.get(Document, w["two"].document_id).chapter_id = chapter.id
    db.commit()
    job_id = generate(api, subject_id=w["subject"].id, chapter_id=chapter.id)["job_id"]
    job = db.get(AiJob, job_id)
    db.refresh(job)
    params = json.loads(job.params_json)
    assert [b["topic_id"] for b in params["batches"]] == [w["T3"].id]
    assert params["chapter_id"] == chapter.id

    both = generate(
        api, subject_id=w["subject"].id, chapter_id=default_chapter_of(db, w),
        topic_ids=[w["T2"].id, w["T3"].id],
    )["job_id"]  # fmt: skip
    job = db.get(AiJob, both)
    db.refresh(job)
    assert [b["topic_id"] for b in json.loads(job.params_json)["batches"]] == [w["T2"].id]

    response = generate(
        api, 422, subject_id=w["subject"].id, chapter_id=default_chapter_of(db, w),
        document_version_id=w["two"].id,
    )  # fmt: skip
    assert response["detail"] == FOREIGN
    other = make_subject(db, "Biologia")
    db.commit()
    response = generate(
        api, 422, subject_id=w["subject"].id, chapter_id=default_chapter(db, other.id)
    )
    assert response["detail"] == FOREIGN


def default_chapter_of(db, w) -> int:
    return db.scalars(
        select(Chapter.id).where(
            Chapter.subject_id == w["subject"].id, Chapter.is_default.is_(True)
        )
    ).one()


def test_legacy_generation_unchanged(db, api, books):
    w = books
    job_id = generate(api, subject_id=w["subject"].id)["job_id"]
    job = db.get(AiJob, job_id)
    db.refresh(job)
    batches = json.loads(job.params_json)["batches"]
    assert [b["topic_id"] for b in batches] == [w["T1"].id, w["T2"].id, w["T3"].id]
    assert job.document_version_id is None


def test_live_job_rules(db, api, fake, books):
    w = books
    params = {"source_policy": "materials", "document_version_id": w["two"].id}
    live = add_job(db, "questions.generate", w["subject"].id, w["two"].id, "running", params)
    again = generate(api, subject_id=w["subject"].id, document_version_id=w["two"].id)
    assert again == {"job_id": live.id}
    other_book = generate(api, 409, subject_id=w["subject"].id, document_version_id=w["one"].id)
    assert other_book["detail"] == GENERATION_RUNNING
    legacy = generate(api, 409, subject_id=w["subject"].id)
    assert legacy["detail"] == GENERATION_RUNNING
    live.status = "done"
    db.commit()
    plain = add_job(db, "questions.generate", w["subject"].id, None, "running", {})
    assert generate(api, subject_id=w["subject"].id) == {"job_id": plain.id}
    chapter = generate(api, 409, subject_id=w["subject"].id, chapter_id=default_chapter_of(db, w))
    assert chapter["detail"] == GENERATION_RUNNING
    assert fake.calls == []


def test_live_jobs_card_shows_the_book(db, client, books):
    w = books
    job, created = questions.start_generation_job(
        db, w["subject"].id, None, 1, "mixed", "mixed", document_version_id=w["two"].id
    )
    assert created
    live = client.get("/api/jobs?live=true").json()
    assert [(j["id"], j["document_title"]) for j in live] == [(job.id, "Rewolucja francuska")]


def test_extension_generation_unchanged(db, client, monkeypatch):
    seen = {}
    original = questions.start_generation_job

    def spy(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return original(*args, **kwargs)

    monkeypatch.setattr(extension.questions, "start_generation_job", spy)
    fake = FakeTextGenerator({"questions.generate": QuestionFaker(), "questions.verify": all_good})
    client.app.dependency_overrides[_generation_dep] = lambda: fake
    world, round_id = weak_extension_round(client, db)
    response = client.post(f"/api/rounds/{round_id}/extension/generate")
    assert response.status_code == 202, response.text
    assert set(seen["kwargs"]) == {"source_policy"}
    assert seen["args"][2] == [world.topics[0].id]
    params = json.loads(db.get(AiJob, response.json()["job_id"]).params_json)
    assert params["chapter_id"] is None and params["document_version_id"] is None


def test_job_out_chapter_and_budget(db, client, books):
    w = books
    default = default_chapter_of(db, w)
    per_book = add_job(
        db, "questions.generate", w["subject"].id, w["two"].id, "running",
        {"document_version_id": w["two"].id},
    )  # fmt: skip

    def call():
        assert client.get("/api/jobs?live=true").status_code == 200

    one = count_statements(client, call)
    body = client.get("/api/jobs?live=true").json()
    assert body[0]["chapter_id"] == default
    assert body[0]["document_title"] == "Rewolucja francuska"
    chapter = Chapter(subject_id=w["subject"].id, name="Druga", is_default=False, ordinal=1)
    db.add(chapter)
    db.commit()
    per_chapter = add_job(
        db, "questions.generate", w["subject"].id, None, "queued", {"chapter_id": chapter.id}
    )
    legacy = add_job(db, "questions.generate", w["subject"].id, None, "queued", {})
    extraction = add_job(db, "topics.extract", w["subject"].id, w["one"].id, "running")
    for _ in range(3):
        add_job(db, "topics.extract", w["subject"].id, w["one"].id, "queued")
    assert count_statements(client, call) == one
    by_id = {j["id"]: j["chapter_id"] for j in client.get("/api/jobs?live=true").json()}
    assert by_id[per_book.id] == default
    assert by_id[per_chapter.id] == chapter.id
    assert by_id[legacy.id] is None
    assert by_id[extraction.id] == default
    assert client.get(f"/api/jobs/{per_chapter.id}").json()["chapter_id"] == chapter.id
