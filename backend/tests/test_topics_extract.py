import pytest
from test_topics_support import (
    fake_consolidation,
    fake_section_topics,
    make_subject,
    make_version,
    passage_chars_in,
    passage_ids_in,
    upgrade,
)

from app.db.session import SessionLocal
from app.features.jobs import service as jobs
from app.features.jobs.models import AiJob
from app.features.providers.fakes import FakeTextGenerator
from app.features.providers.registry import _generation_dep
from app.features.topics import service
from app.features.topics.models import LearningObjective, Term, Topic, TopicPassage


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def fake():
    return FakeTextGenerator(
        {"topics.extract_section": fake_section_topics, "topics.consolidate": fake_consolidation}
    )


@pytest.fixture
def api(client, fake):
    client.app.dependency_overrides[_generation_dep] = lambda: fake
    return client


def extract(api, version_id: int):
    return api.post("/api/topics/extract", json={"document_version_id": version_id})


def test_sections_respect_12000_chars(db, api, fake):
    subject = make_subject(db)
    version, passages = make_version(db, subject.id)
    response = extract(api, version.id)
    assert response.status_code == 202, response.text
    calls = [c for c in fake.calls if c["task"] == "topics.extract_section"]
    assert len(calls) == 3  # 28 passages of 900 chars → 13 + 13 + 2
    excluded = {p.id for p in passages if p.excluded}
    seen: list[int] = []
    for call in calls:
        assert passage_chars_in(call["messages"]) <= service.SECTION_CHAR_LIMIT == 12000
        seen += passage_ids_in(call["messages"])
    assert not excluded & set(seen)
    assert seen == [p.id for p in passages if not p.excluded]


def test_passages_tagged_and_untrusted_warning(db, api, fake):
    subject = make_subject(db)
    version, _ = make_version(db, subject.id, count=3, excluded=())
    extract(api, version.id)
    call = fake.calls[0]
    assert '<passage id="' in call["messages"][0].content
    assert "ignore" in call["system"].lower()
    assert "polish" in call["system"].lower()


def test_single_section_no_consolidate_call(db, api, fake):
    subject = make_subject(db)
    version, _ = make_version(db, subject.id, count=10, excluded=())
    extract(api, version.id)
    assert [c["task"] for c in fake.calls] == ["topics.extract_section"]
    assert db.query(Topic).count() == 3


def test_multi_section_consolidates(db, api, fake):
    subject = make_subject(db)
    version, _ = make_version(db, subject.id)
    extract(api, version.id)
    assert [c["task"] for c in fake.calls][-1] == "topics.consolidate"
    titles = sorted(t.title for t in db.query(Topic))
    assert titles == [f"Scalony {n}" for n in range(4)]  # 3 + 3 + 2 section topics → 4 merged


def test_topics_objectives_terms_links_saved(db, api, fake):
    subject = make_subject(db)
    version, passages = make_version(db, subject.id, count=10, excluded=(2,))
    extract(api, version.id)
    topics = db.query(Topic).order_by(Topic.ordinal).all()
    assert [t.origin for t in topics] == ["ai"] * 3
    assert all(t.status == "active" and t.subject_id == subject.id for t in topics)
    assert db.query(LearningObjective).count() == 3
    assert {t.term for t in db.query(Term)} == {f"Pojęcie {passages[0].id}-{n}" for n in range(3)}
    linked = {link.passage_id for link in db.query(TopicPassage)}
    assert linked == {p.id for p in passages if not p.excluded}

    tree = api.get("/api/topics", params={"subject_id": subject.id}).json()
    assert len(tree) == 3
    assert set(tree[0]) == {
        "id", "title", "description", "origin", "status", "objectives", "passage_count",
        "question_count", "children", "question_counts", "chapter_id",
    }  # fmt: skip
    assert tree[0]["objectives"][0]["text"].startswith("Wyjaśnić")
    assert sum(t["passage_count"] for t in tree) == 9
    terms = api.get("/api/topics/terms", params={"subject_id": subject.id}).json()
    assert len(terms) == 3 and set(terms[0]) == {"term", "definition", "topic_id"}
    assert len(service.get_glossary(db, subject.id)) == 3


def test_invalid_passage_ids_dropped(db, api):
    subject = make_subject(db)
    version, passages = make_version(db, subject.id, count=6, excluded=(1,))
    other, other_passages = make_version(db, subject.id, count=2, excluded=())
    bogus = (999999, other_passages[0].id, passages[1].id)
    fake = FakeTextGenerator(
        {"topics.extract_section": lambda m: fake_section_topics(m, extra_ids=bogus)}
    )
    api.app.dependency_overrides[_generation_dep] = lambda: fake
    extract(api, version.id)
    linked = {link.passage_id for link in db.query(TopicPassage)}
    assert linked == {p.id for p in passages if not p.excluded}


def test_job_progress_counts(db, api):
    subject = make_subject(db)
    version, _ = make_version(db, subject.id)
    job_id = extract(api, version.id).json()["job_id"]
    body = api.get(f"/api/jobs/{job_id}").json()
    assert body["kind"] == "topics.extract"
    assert body["status"] == "done"
    assert body["steps_total"] == body["steps_done"] == 4  # 3 sections + consolidate/save
    assert body["summary"] == {"sections_total": 3, "sections_done": 3, "topics_created": 4}


def test_extract_resumes_after_provider_failure(db, api, fake):
    from app.features.providers.types import ProviderError

    subject = make_subject(db)
    version, _ = make_version(db, subject.id)
    calls = {"n": 0}

    def flaky(messages):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ProviderError("timeout", "openai", "The provider did not respond in time.")
        return fake_section_topics(messages)

    fake.responses["topics.extract_section"] = flaky
    job_id = extract(api, version.id).json()["job_id"]
    body = api.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "failed"
    assert body["error_message"] == "The provider did not respond in time."
    assert db.query(Topic).count() == 0
    assert api.post(f"/api/jobs/{job_id}/resume").status_code == 202
    assert calls["n"] == 4  # section 0 not repeated
    assert api.get(f"/api/jobs/{job_id}").json()["status"] == "done"


def test_extract_not_imported_409(db, api):
    subject = make_subject(db)
    version, _ = make_version(db, subject.id, status="too_little_text")
    response = extract(api, version.id)
    assert response.status_code == 409
    assert response.json()["detail"] == "The document was not imported correctly."
    assert extract(api, 99999).status_code == 404


def test_extract_twice_while_running_409(db, api):
    subject = make_subject(db)
    version, _ = make_version(db, subject.id)
    jobs.create_job(db, "topics.extract", subject.id, {}, 1, document_version_id=version.id)
    response = extract(api, version.id)
    assert response.status_code == 409
    assert response.json()["detail"] == "This document is already being analysed."
    assert db.query(AiJob).count() == 1
