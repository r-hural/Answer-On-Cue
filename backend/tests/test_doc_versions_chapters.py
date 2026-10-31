import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_chapters_support import add_job, fake_chapter_consolidation
from test_coverage_support import make_answer, make_round
from test_doc_versions_support import (
    V1,
    V2,
    document_text,
    paragraph,
    replace_world,
    statements,
    writes,
)
from test_questions_support import QuestionFaker, all_good
from test_topics_support import (
    fake_consolidation,
    fake_section_topics,
    make_subject,
    make_version,
    passage_ids_in,
    upgrade,
)

from app.db.session import SessionLocal
from app.features.jobs.models import AiJob
from app.features.library.models import Document, DocumentVersion
from app.features.providers.fakes import FakeTextGenerator
from app.features.providers.registry import _generation_dep
from app.features.questions import service as questions
from app.features.questions.models import QuestionVersion
from app.features.topics import service as topics
from app.features.topics.models import LearningObjective, Topic
from app.features.topics.prompts import SectionTerm, SectionTopic, SectionTopics

VERSION_REPLACED = "A newer version of this document exists. Use the latest version."
NOTHING_NEW = (
    "This version has no new passages to analyse: its topics came over from the previous version."
)
NO_CHANGED_TOPICS = (
    "No topic has new or changed passages in this version. Analyse its new passages first, "
    "or choose All topics."
)
OLD = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def fake():
    return FakeTextGenerator(
        {
            "topics.extract_section": fake_section_topics,
            "topics.consolidate": fake_consolidation,
            "questions.generate": QuestionFaker(),
            "questions.verify": all_good,
        }
    )


@pytest.fixture
def api(client, fake):
    client.app.dependency_overrides[_generation_dep] = lambda: fake
    return client


def version_ids(db, document_id: int) -> list[int]:
    db.expire_all()
    return [v.id for v in db.get(Document, document_id).versions]


def finished(db, kind: str, subject_id: int, version_id: int, at: datetime = OLD) -> AiJob:
    job = add_job(db, kind, subject_id, version_id, "done")
    job.updated_at = at
    db.commit()
    return job


@pytest.fixture
def world(db, settings) -> dict:
    """The replace world with version 1 analysed and generated (jobs done), not replaced yet."""
    data = replace_world(db, settings)
    v1 = version_ids(db, data["a"])[0]
    data["v1"] = v1
    finished(db, "topics.extract", data["subject"], v1)
    finished(db, "questions.generate", data["subject"], v1, OLD + timedelta(hours=1))
    return data


def replace(client, world, paragraphs=V2) -> int:
    change = client.post(
        f"/api/documents/{world['a']}/versions/preview",
        files={"file": ("demokracja-2.txt", document_text(paragraphs), "text/plain")},
    ).json()
    applied = client.post(
        f"/api/documents/{world['a']}/versions/{change['token']}/apply",
        json={"fingerprint": change["fingerprint"]},
    )
    assert applied.status_code == 201, applied.text
    return applied.json()["document"]["latest_version"]["id"]


def extract(api, version_id: int, status: int = 202):
    response = api.post("/api/topics/extract", json={"document_version_id": version_id})
    assert response.status_code == status, response.text
    return response.json()


def generate(api, status: int = 202, **body):
    response = api.post("/api/questions/generate", json={"per_topic": 1, **body})
    assert response.status_code == status, response.text
    return response.json()


def calls(fake, task: str) -> list[dict]:
    return [c for c in fake.calls if c["task"] == task]


def batches(db, job_id: int) -> list[int]:
    db.expire_all()
    return [b["topic_id"] for b in json.loads(db.get(AiJob, job_id).params_json)["batches"]]


def new_passage(db, version_id: int) -> int:
    version = db.get(DocumentVersion, version_id)
    return next(p.id for p in version.passages if p.previous_id is None)


def test_analyse_later_version_sends_only_new_passages(db, api, fake, world):
    v2 = replace(api, world)
    added = new_passage(db, v2)
    job = extract(api, v2)
    sections = calls(fake, "topics.extract_section")
    assert [passage_ids_in(c["messages"]) for c in sections] == [[added]]
    db.expire_all()
    assert json.loads(db.get(AiJob, job["job_id"]).params_json)["sections"] == [[added]]
    consolidate = calls(fake, "topics.consolidate")[0]["messages"][0].content
    for key in ("t1", "t2", "t3"):
        assert f"[e{world['topics'][key]}]" in consolidate

    nothing = replace(api, world, [paragraph(1, "oligarchii"), *V2[1:]])
    assert extract(api, nothing, 409)["detail"] == NOTHING_NEW


def test_never_analysed_document_sends_everything(db, api, fake, settings):
    from app.features.library import service as library

    subject = make_subject(db, "Nowy")
    db.commit()
    document = library.create_document(db, settings, subject.id, "a.txt", document_text(V1), None)
    library.add_version(db, settings, document.id, "b.txt", document_text(V2))
    v2 = version_ids(db, document.id)[1]
    extract(api, v2)
    sent = [i for c in calls(fake, "topics.extract_section") for i in passage_ids_in(c["messages"])]
    usable = [p.id for p in db.get(DocumentVersion, v2).passages if not p.excluded]
    assert sent == usable and len(sent) == 8


def test_older_version_is_read_only(db, api, world):
    replace(api, world)
    assert extract(api, world["v1"], 409)["detail"] == VERSION_REPLACED
    refused = generate(api, 409, subject_id=world["subject"], document_version_id=world["v1"])
    assert refused["detail"] == VERSION_REPLACED


def test_per_book_later_version_covers_changed_topics(db, api, fake, world):
    v2 = replace(api, world)
    job = generate(api, subject_id=world["subject"], document_version_id=v2)
    assert batches(db, job["job_id"]) == [world["topics"]["t2"]]

    extract(api, v2)
    added = new_passage(db, v2)
    t4 = db.scalars(select(Topic).where(Topic.subject_id == world["subject"])).all()[-1]
    assert t4.id not in world["topics"].values()
    job = generate(api, subject_id=world["subject"], document_version_id=v2)
    assert batches(db, job["job_id"]) == [world["topics"]["t2"], t4.id]
    prompts = [c["messages"][0].content for c in calls(fake, "questions.generate")]
    assert f'<passage id="{added}">' in prompts[-1]

    b_version = version_ids(db, world["b"])[0]
    job = generate(api, subject_id=world["subject"], document_version_id=b_version)
    assert batches(db, job["job_id"]) == [world["topics"]["tb"]]


def test_no_changed_topics_409(db, api, world):
    v2 = replace(api, world, [*V1, paragraph(9)])
    refused = generate(api, 409, subject_id=world["subject"], document_version_id=v2)
    assert refused["detail"] == NO_CHANGED_TOPICS


def test_per_book_objectives(db, api, fake, world):
    t2 = world["topics"]["t2"]
    for objective in db.scalars(select(LearningObjective).where(LearningObjective.topic_id == t2)):
        db.delete(objective)
    db.add(LearningObjective(topic_id=t2, text="Cel z Rzymu", ordinal=0, document_id=world["b"]))
    db.add(LearningObjective(topic_id=t2, text="Cel z Aten", ordinal=1, document_id=world["a"]))
    db.add(LearningObjective(topic_id=t2, text="Cel wspólny", ordinal=2, document_id=None))
    db.commit()
    v2 = replace(api, world)
    generate(api, subject_id=world["subject"], document_version_id=v2)
    prompt = calls(fake, "questions.generate")[-1]["messages"][0].content
    assert "Cel z Aten" in prompt and "Cel wspólny" in prompt and "Cel z Rzymu" not in prompt
    db.expire_all()
    saved = db.scalars(select(QuestionVersion).order_by(QuestionVersion.id.desc())).first()
    athens = db.scalars(
        select(LearningObjective).where(LearningObjective.text == "Cel z Aten")
    ).one()
    assert saved.objective_id == athens.id

    generate(api, subject_id=world["subject"], topic_ids=[t2])
    prompt = calls(fake, "questions.generate")[-1]["messages"][0].content
    assert "Cel z Aten" in prompt and "Cel wspólny" in prompt and "Cel z Rzymu" in prompt


def _same_objective(messages):
    ids = passage_ids_in(messages)
    return SectionTopics(
        topics=[
            SectionTopic(
                title=f"Temat {ids[0]}",
                description="Opis",
                objectives=["Wspólny cel", f"Cel {ids[0]}"],
                passage_ids=ids,
                terms=[SectionTerm(term=f"Pojęcie {ids[0]}", definition="Definicja")],
            )
        ]
    )


def test_objective_provenance_on_extract(db, client):
    fake = FakeTextGenerator(
        {
            "topics.extract_section": _same_objective,
            "topics.consolidate": fake_chapter_consolidation,
        }
    )
    client.app.dependency_overrides[_generation_dep] = lambda: fake
    subject = make_subject(db, "Historia PL")
    one, _ = make_version(db, subject.id, count=2, excluded=())
    two, _ = make_version(db, subject.id, count=2, excluded=())
    extract(client, one.id)
    db.expire_all()
    objectives = db.scalars(select(LearningObjective)).all()
    assert {o.document_id for o in objectives} == {one.document_id}
    extract(client, two.id)
    db.expire_all()
    by_text = {o.text: o for o in db.scalars(select(LearningObjective))}
    assert by_text["Wspólny cel"].document_id is None
    own = [o for t, o in by_text.items() if t.startswith("Cel ")]
    assert {o.document_id for o in own} == {one.document_id, two.document_id}

    topic = db.get(Topic, by_text["Wspólny cel"].topic_id)
    owned = [o for o in by_text.values() if o.topic_id == topic.id]
    parts = [
        {"title": "Część 1", "objective_ids": [owned[0].id]},
        {"title": "Część 2", "objective_ids": [o.id for o in owned[1:]]},
    ]
    response = client.post(f"/api/topics/{topic.id}/split", json={"parts": parts})
    assert response.status_code == 200, response.text
    db.expire_all()
    assert {o.id: o.document_id for o in db.scalars(select(LearningObjective))} == {
        o.id: o.document_id for o in by_text.values()
    }


def test_book_flags_across_versions(db, api, world):
    before = api.get(f"/api/documents/{world['a']}").json()["versions"][0]
    v2 = replace(api, world)

    def latest():
        listed = api.get(f"/api/subjects/{world['subject']}/documents").json()
        return next(d["latest_version"] for d in listed if d["id"] == world["a"])

    flags = latest()
    assert flags["id"] == v2
    assert flags["topics_extracted_at"] == before["topics_extracted_at"] is not None
    assert flags["questions_generated_at"] == before["questions_generated_at"] is not None
    assert flags["topic_count"] == 2
    extract(api, v2)
    flags = latest()
    assert flags["topics_extracted_at"] > flags["questions_generated_at"]


def _documents_budget(api, db, settings, versions: int) -> int:
    from app.features.library import service as library

    subject = make_subject(db, f"Budżet {versions}")
    db.commit()
    for d in range(2):
        paragraphs = [paragraph(n, f"książce {d}") for n in range(1, 4)]
        document = library.create_document(
            db, settings, subject.id, "a.txt", document_text(paragraphs), f"Książka {d}"
        )
        for n in range(2, versions + 1):
            paragraphs = [*paragraphs, paragraph(10 + n, f"książce {d}")]
            library.add_version(db, settings, document.id, f"a{n}.txt", document_text(paragraphs))
            finished(db, "topics.extract", subject.id, version_ids(db, document.id)[-1])
    path = f"/api/subjects/{subject.id}/documents"
    seen = statements(lambda: api.get(path))
    assert writes(seen) == []
    return len(seen)


def test_documents_budget(api, db, settings):
    assert _documents_budget(api, db, settings, 1) == _documents_budget(api, db, settings, 3)


def _answered_round(db, world, questions_: list[int], at: datetime) -> None:
    round_ = make_round(db, world["subject"], at=at)
    for n, version_id in enumerate(questions_):
        make_answer(db, round_, db.get(QuestionVersion, version_id), at=at, band="good", ordinal=n)
    db.commit()


def test_document_detail_usage(api, db, world):
    at = datetime(2026, 10, 20, 9, tzinfo=UTC)
    _answered_round(db, world, [world["q"]["q1"], world["q"]["q2"]], at)
    replace(api, world)

    def usage():
        versions = api.get(f"/api/documents/{world['a']}").json()["versions"]
        return [(v["version"], v["answer_count"], v["round_count"]) for v in versions]

    assert usage() == [(1, 2, 1), (2, 0, 0)]
    small = statements(lambda: api.get(f"/api/documents/{world['a']}"))
    assert writes(small) == []
    for n in range(2):
        _answered_round(db, world, [world["q"]["q1"]], at + timedelta(days=n + 1))
    assert usage() == [(1, 4, 3), (2, 0, 0)]
    large = statements(lambda: api.get(f"/api/documents/{world['a']}"))
    assert len(small) == len(large) and writes(large) == []


def test_selection_functions(db, api, world):
    subject = world["subject"]
    v2 = replace(api, world)
    chapter = db.get(Document, world["a"]).chapter_id
    b_version = version_ids(db, world["b"])[0]
    cases = [
        {"document_version_id": v2},
        {"document_version_id": b_version},
        {"chapter_id": chapter},
        {"topic_ids": [world["topics"]["t1"], world["topics"]["tb"]]},
        {},
    ]
    for case in cases:
        topic_ids = case.get("topic_ids")
        keywords = {k: v for k, v in case.items() if k != "topic_ids"}
        expected = questions.generation_topic_ids(db, subject, topic_ids, **keywords)
        job = generate(api, subject_id=subject, **case)
        assert batches(db, job["job_id"]) == expected, case
        db.get(AiJob, job["job_id"]).status = "done"
        db.commit()
    for keywords, detail in (
        ({"document_version_id": world["v1"]}, VERSION_REPLACED),
        ({"topic_ids": [999999]}, "Analyze the materials first to identify the topics."),
    ):
        topic_ids = keywords.pop("topic_ids", None)
        with pytest.raises(HTTPException) as raised:
            questions.generation_topic_ids(db, subject, topic_ids, **keywords)
        assert raised.value.detail == detail
        body = {"topic_ids": topic_ids} if topic_ids else keywords
        assert generate(api, 409, subject_id=subject, **body)["detail"] == detail

    sections = topics.extraction_sections(db, db.get(DocumentVersion, v2))
    extract(api, v2)
    db.expire_all()
    job = db.scalars(select(AiJob).where(AiJob.kind == "topics.extract")).all()[-1]
    assert json.loads(job.params_json)["sections"] == sections == [[new_passage(db, v2)]]
