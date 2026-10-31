import json

import pytest
from sqlalchemy import select
from test_chapters_support import E_REF_RE, fake_chapter_consolidation, no_writes
from test_reports_history import count_statements
from test_topics_support import (
    REF_RE,
    fake_consolidation,
    fake_section_topics,
    make_subject,
    make_topic,
    make_version,
    passage_ids_in,
    upgrade,
)

from app.db.session import SessionLocal
from app.features.jobs import service as jobs
from app.features.jobs.models import AiJob
from app.features.library.models import Chapter, Document
from app.features.providers.fakes import FakeTextGenerator
from app.features.providers.registry import _generation_dep
from app.features.topics.models import LearningObjective, Term, Topic, TopicPassage
from app.features.topics.prompts import ConsolidatedTopic, Consolidation

MISMATCH = "Topics from different chapters cannot be merged. Move one first."


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
            "topics.consolidate": fake_chapter_consolidation,
        }
    )


@pytest.fixture
def api(client, fake):
    client.app.dependency_overrides[_generation_dep] = lambda: fake
    return client


def extract(api, version_id: int) -> dict:
    response = api.post("/api/topics/extract", json={"document_version_id": version_id})
    assert response.status_code == 202, response.text
    return api.get(f"/api/jobs/{response.json()['job_id']}").json()


def default_chapter(db, subject_id: int) -> int:
    return db.scalars(
        select(Chapter.id).where(Chapter.subject_id == subject_id, Chapter.is_default.is_(True))
    ).one()


def second_chapter(db, subject_id: int, name: str = "Rewolucja francuska") -> int:
    chapter = Chapter(subject_id=subject_id, name=name, is_default=False, ordinal=1)
    db.add(chapter)
    db.commit()
    return chapter.id


def put_in(db, version, chapter_id: int) -> None:
    db.get(Document, version.document_id).chapter_id = chapter_id
    db.commit()


def topics_of(db, version_or_chapter_id: int) -> list[Topic]:
    db.expire_all()
    return list(
        db.scalars(
            select(Topic).where(Topic.chapter_id == version_or_chapter_id).order_by(Topic.id)
        )
    )


def snapshot(db, topic_ids: list[int]) -> tuple:
    db.expire_all()
    topics = [
        (t.id, t.title, t.description, t.status, t.chapter_id, t.ordinal)
        for t in db.scalars(select(Topic).where(Topic.id.in_(topic_ids)).order_by(Topic.id))
    ]
    links = sorted(
        (link.topic_id, link.passage_id)
        for link in db.scalars(select(TopicPassage).where(TopicPassage.topic_id.in_(topic_ids)))
    )
    objectives = sorted(
        (o.id, o.topic_id, o.text, o.ordinal)
        for o in db.scalars(
            select(LearningObjective).where(LearningObjective.topic_id.in_(topic_ids))
        )
    )
    terms = sorted(
        (t.id, t.topic_id, t.term, t.definition)
        for t in db.scalars(select(Term).where(Term.topic_id.in_(topic_ids)))
    )
    return topics, links, objectives, terms


def consolidate_calls(fake) -> list[dict]:
    return [c for c in fake.calls if c["task"] == "topics.consolidate"]


def test_first_book_in_empty_chapter_behaves_as_before(db, api, fake):
    subject = make_subject(db)
    version, _ = make_version(db, subject.id, count=10, excluded=())
    chapter = second_chapter(db, subject.id)
    put_in(db, version, chapter)
    job = extract(api, version.id)
    assert [c["task"] for c in fake.calls] == ["topics.extract_section"]
    assert len(topics_of(db, chapter)) == 3
    assert topics_of(db, default_chapter(db, subject.id)) == []
    assert job["summary"].get("topics_extended", 0) == 0


def test_second_book_same_chapter_sees_existing_topics(db, api, fake):
    subject = make_subject(db)
    first, _ = make_version(db, subject.id, count=10, excluded=())
    extract(api, first.id)
    default = default_chapter(db, subject.id)
    existing = topics_of(db, default)
    assert len(existing) == 3
    gone = make_topic(db, subject.id, "Usunięty temat")
    gone.status = "removed"
    other = make_topic(db, subject.id, "Temat innego rozdziału")
    other.chapter_id = second_chapter(db, subject.id)
    db.commit()

    second, passages = make_version(db, subject.id, count=10, excluded=())
    fake.calls.clear()
    job = extract(api, second.id)
    assert [c["task"] for c in fake.calls] == ["topics.extract_section", "topics.consolidate"]
    call = consolidate_calls(fake)[0]
    message = call["messages"][0].content
    listed = E_REF_RE.findall(message)
    assert listed == [f"e{t.id}" for t in existing]
    for topic in existing:
        assert f"[e{topic.id}] {topic.title}: {topic.description}" in message
    assert f"e{gone.id}" not in listed and f"e{other.id}" not in listed
    assert "never put two [e" in call["system"]

    db.expire_all()
    extended = db.get(Topic, existing[0].id)
    assert (extended.title, extended.description) == (existing[0].title, existing[0].description)
    section_ids = passage_ids_in([fake.calls[0]["messages"][0]])
    first_third = section_ids[0::3]
    links = set(
        db.scalars(select(TopicPassage.passage_id).where(TopicPassage.topic_id == extended.id))
    )
    assert set(first_third) <= links
    objectives = list(
        db.scalars(
            select(LearningObjective.text)
            .where(LearningObjective.topic_id == extended.id)
            .order_by(LearningObjective.ordinal)
        )
    )
    assert objectives[-1] == f"Wyjaśnić zagadnienie {section_ids[0]}-0"
    assert len(objectives) == len(set(objectives)) == 2
    term = db.scalars(select(Term).where(Term.term == f"Pojęcie {section_ids[0]}-0")).one()
    assert term.topic_id == extended.id
    assert job["summary"]["topics_extended"] == 1
    assert job["summary"]["topics_created"] == 2
    assert len([t for t in topics_of(db, default) if t.status == "active"]) == 5
    assert all(p.document_version_id == second.id for p in passages)


def test_other_chapter_topics_are_never_touched(db, api, fake):
    subject = make_subject(db)
    first, _ = make_version(db, subject.id, count=10, excluded=())
    extract(api, first.id)
    default = default_chapter(db, subject.id)
    kept = [t.id for t in topics_of(db, default)]
    before = snapshot(db, kept)

    second, _ = make_version(db, subject.id)  # three sections: the consolidate call is made
    chapter = second_chapter(db, subject.id)
    put_in(db, second, chapter)
    fake.calls.clear()
    job = extract(api, second.id)
    calls = consolidate_calls(fake)
    assert len(calls) == 1
    assert E_REF_RE.findall(calls[0]["messages"][0].content) == []
    assert snapshot(db, kept) == before
    assert len(topics_of(db, chapter)) == job["summary"]["topics_created"] > 0
    assert job["summary"].get("topics_extended", 0) == 0


def test_unknown_or_foreign_e_refs_are_ignored(db, client):
    subject = make_subject(db)
    _, old_passages = make_version(db, subject.id, count=4, excluded=())
    a = make_topic(db, subject.id, "Ateny", old_passages[:2], ("Omówić Ateny",))
    b = make_topic(db, subject.id, "Sparta", old_passages[2:], ("Omówić Spartę",))
    gone = make_topic(db, subject.id, "Usunięty")
    gone.status = "removed"
    foreign = make_topic(db, subject.id, "Obcy")
    foreign.chapter_id = second_chapter(db, subject.id)
    db.commit()
    untouched = snapshot(db, [b.id, gone.id, foreign.id])

    def consolidation(messages):
        refs = [r for m in messages for r in REF_RE.findall(m.content)]
        return Consolidation(
            topics=[
                ConsolidatedTopic(
                    title="Obce", description="d", merged_from=[refs[0], f"e{foreign.id}"]
                ),
                ConsolidatedTopic(
                    title="Usunięte", description="d", merged_from=[refs[1], f"e{gone.id}"]
                ),
                ConsolidatedTopic(title="Brak", description="d", merged_from=[refs[2], "e999999"]),
                ConsolidatedTopic(
                    title="Dwa", description="d", merged_from=[refs[3], f"[e{a.id}]", f"e{b.id}"]
                ),
            ]
        )

    def sections(messages):
        result = fake_section_topics(messages)
        return result.model_copy(update={"topics": result.topics * 2})

    fake = FakeTextGenerator(
        {"topics.extract_section": sections, "topics.consolidate": consolidation}
    )
    client.app.dependency_overrides[_generation_dep] = lambda: fake
    version, _ = make_version(db, subject.id, count=3, excluded=())
    job = extract(client, version.id)
    assert job["summary"]["topics_extended"] == 1
    assert snapshot(db, [b.id, gone.id, foreign.id]) == untouched
    db.expire_all()
    titles = {t.title for t in db.scalars(select(Topic).where(Topic.status == "active"))}
    assert {"Obce", "Usunięte", "Brak"} <= titles
    assert "Dwa" not in titles  # attached to Ateny, which keeps its own title
    assert db.get(Topic, a.id).title == "Ateny"
    assert db.get(Topic, gone.id).status == "removed"


def test_job_started_before_migration_resolves_chapter(db, api, fake, settings):
    from app.features.topics import service as topics

    subject = make_subject(db)
    version, _ = make_version(db, subject.id, count=10, excluded=())
    job = topics.start_extraction(db, version.id)
    params = json.loads(job.params_json)
    params.pop("chapter_id")
    job.params_json = json.dumps(params)
    db.commit()
    chapter = second_chapter(db, subject.id)
    put_in(db, version, chapter)
    jobs.run_job(job.id, settings, fake)
    db.expire_all()
    assert db.get(AiJob, job.id).status == "done"
    assert len(topics_of(db, chapter)) == 3


def test_start_extraction_records_the_chapter(db, api, fake):
    subject = make_subject(db)
    version, _ = make_version(db, subject.id, count=10, excluded=())
    chapter = second_chapter(db, subject.id)
    put_in(db, version, chapter)
    job_id = api.post("/api/topics/extract", json={"document_version_id": version.id}).json()
    params = json.loads(db.get(AiJob, job_id["job_id"]).params_json)
    assert params["chapter_id"] == chapter


def test_merge_needs_same_chapter(db, client):
    subject = make_subject(db)
    a = make_topic(db, subject.id, "Ateny")
    b = make_topic(db, subject.id, "Sparta")
    c = make_topic(db, subject.id, "Paryż")
    chapter = second_chapter(db, subject.id)
    c.chapter_id = chapter
    d = make_topic(db, subject.id, "Wersal")
    d.chapter_id = chapter
    db.commit()
    refused = client.post("/api/topics/merge", json={"topic_ids": [a.id, c.id], "title": "X"})
    assert refused.status_code == 422 and refused.json()["detail"] == MISMATCH
    merged = client.post("/api/topics/merge", json={"topic_ids": [c.id, d.id], "title": "Francja"})
    assert merged.status_code == 200, merged.text
    assert merged.json()["chapter_id"] == chapter
    same = client.post("/api/topics/merge", json={"topic_ids": [a.id, b.id], "title": "Grecja"})
    assert same.json()["chapter_id"] == default_chapter(db, subject.id)


def test_split_keeps_chapter(db, client):
    subject = make_subject(db)
    _, passages = make_version(db, subject.id, count=2, excluded=())
    topic = make_topic(db, subject.id, "Rewolucja", passages, ("Omówić przyczyny", "Omówić skutki"))
    chapter = second_chapter(db, subject.id)
    topic.chapter_id = chapter
    db.commit()
    objectives = list(
        db.scalars(select(LearningObjective.id).where(LearningObjective.topic_id == topic.id))
    )
    body = {
        "parts": [
            {"title": "Przyczyny", "objective_ids": [objectives[0]]},
            {"title": "Skutki", "objective_ids": [objectives[1]]},
        ]
    }
    response = client.post(f"/api/topics/{topic.id}/split", json=body)
    assert response.status_code == 200, response.text
    assert [part["chapter_id"] for part in response.json()] == [chapter, chapter]


def test_topics_filter_by_chapter(db, client):
    subject = make_subject(db)
    db.commit()
    chapter = second_chapter(db, subject.id)
    default = default_chapter(db, subject.id)

    def add(n: int) -> None:
        for i in range(n):
            topic = make_topic(db, subject.id, f"Temat {i}", ordinal=i)
            if i % 2:
                topic.chapter_id = chapter
            db.commit()

    def tree(**params):
        response = client.get("/api/topics", params={"subject_id": subject.id, **params})
        assert response.status_code == 200
        return response.json()

    add(2)
    plain = count_statements(client, lambda: tree())
    filtered = count_statements(client, lambda: tree(chapter_id=chapter))
    assert plain == filtered
    add(6)
    assert count_statements(client, lambda: tree()) == plain
    assert count_statements(client, lambda: tree(chapter_id=chapter)) == filtered
    no_writes(lambda: tree(chapter_id=chapter))
    in_chapter = tree(chapter_id=chapter)
    assert len(in_chapter) == 4 and {t["chapter_id"] for t in in_chapter} == {chapter}
    assert {t["chapter_id"] for t in tree(chapter_id=default)} == {default}
    assert len(tree()) == 8


def test_existing_fake_consolidation_still_works(db, client):
    fake = FakeTextGenerator(
        {"topics.extract_section": fake_section_topics, "topics.consolidate": fake_consolidation}
    )
    client.app.dependency_overrides[_generation_dep] = lambda: fake
    subject = make_subject(db)
    version, _ = make_version(db, subject.id, count=10, excluded=())
    extract(client, version.id)
    again, _ = make_version(db, subject.id, count=10, excluded=())
    job = extract(client, again.id)
    assert job["status"] == "done"
    assert job["summary"]["topics_created"] == 2 and job["summary"].get("topics_extended", 0) == 0
