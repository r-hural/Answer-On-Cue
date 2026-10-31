"""Cost estimates and the spending ceiling together with document versions:

(a) the cost estimate equals the job that actually starts for a replaced book: extraction of a
    later version (only its new passages) and per-book generation after a replace;
(b) previewing and applying a new document version and keeping a question make no provider
    call and write no usage_event row, with a spending ceiling set;
(c) extracting a later version that the spending ceiling refuses is stored as a failed,
    resumable job and leaves nothing stuck: the subject can be replaced again and the job
    resumes once the ceiling allows it."""

import json
from datetime import datetime

import pytest
from sqlalchemy import func, select
from test_costs_support import ZONE, seed_usage
from test_doc_versions_support import V2, document_text, paragraph, replace_world, statements
from test_questions_support import QuestionFaker, all_good
from test_topics_support import fake_consolidation, fake_section_topics, upgrade

from app.db.session import SessionLocal
from app.features.costs import estimates
from app.features.jobs import service as jobs
from app.features.jobs.models import AiJob
from app.features.library.models import Document, DocumentVersion
from app.features.providers.fakes import FakeTextGenerator
from app.features.providers.models import UsageEventRow
from app.features.providers.registry import _generation_dep
from app.features.questions import service as questions
from app.features.topics import service as topics
from app.features.topics.models import TopicPassage

VERSION_REPLACED = "A newer version of this document exists. Use the latest version."


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


@pytest.fixture
def world(db, settings) -> dict:
    """The replace world with version 1 analysed and generated."""
    data = replace_world(db, settings)
    v1 = db.get(Document, data["a"]).versions[0].id
    for kind in ("topics.extract", "questions.generate"):
        job = jobs.create_job(db, kind, data["subject"], {}, 1, document_version_id=v1)
        job.status = "done"
    db.commit()
    data["v1"] = v1
    return data


def replace(client, world, paragraphs=V2) -> int:
    change = client.post(
        f"/api/documents/{world['a']}/versions/preview",
        files={"file": ("demokracja-2.txt", document_text(paragraphs), "text/plain")},
    )
    assert change.status_code == 200, change.text
    body = change.json()
    applied = client.post(
        f"/api/documents/{world['a']}/versions/{body['token']}/apply",
        json={"fingerprint": body["fingerprint"]},
    )
    assert applied.status_code == 201, applied.text
    return applied.json()["document"]["latest_version"]["id"]


def set_ceiling(client, value: float) -> None:
    response = client.put("/api/settings", json={"spending_ceiling_usd": value})
    assert response.status_code == 200, response.text


def job_params(db, job_id: int) -> dict:
    db.expire_all()
    return json.loads(db.get(AiJob, job_id).params_json)


def usage_count(db) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(UsageEventRow))


# ---- (a) the estimate is the job ----


def test_extraction_estimate_of_a_later_version_is_the_job(api, db, world):
    v2 = replace(api, world)
    estimate = api.get(f"/api/costs/estimate/extraction?document_version_id={v2}").json()
    expected = topics.extraction_sections(db, db.get(DocumentVersion, v2))
    added = [p.id for p in db.get(DocumentVersion, v2).passages if p.previous_id is None]
    assert expected == [added]  # only the new passage

    started = api.post("/api/topics/extract", json={"document_version_id": v2})
    assert started.status_code == 202, started.text
    params = job_params(db, started.json()["job_id"])
    assert params["sections"] == expected
    assert estimate["sections"] == len(params["sections"])
    job_side = estimates.estimate_job(db, api.app.state.settings, "topics.extract", params)
    assert estimate["usd"] == job_side.usd and estimate["usd"] > 0


def test_per_book_generation_estimate_after_a_replace_is_the_job(api, db, world):
    v2 = replace(api, world)
    body = {"subject_id": world["subject"], "per_topic": 7, "document_version_id": v2}
    estimate = api.post("/api/costs/estimate/generation", json=body).json()
    picked = questions.generation_topic_ids(db, world["subject"], None, document_version_id=v2)
    assert picked == [world["topics"]["t2"]]  # the topic whose passage changed

    started = api.post("/api/questions/generate", json=body)
    assert started.status_code == 202, started.text
    params = job_params(db, started.json()["job_id"])
    assert [b["topic_id"] for b in params["batches"]] == [picked[0], picked[0]]
    assert [b["count"] for b in params["batches"]] == [5, 2]
    assert {b["document_version_id"] for b in params["batches"]} == {v2}
    assert estimate["questions"] == sum(b["count"] for b in params["batches"]) == 7
    job_side = estimates.estimate_job(db, api.app.state.settings, "questions.generate", params)
    assert estimate["usd"] == job_side.usd and estimate["usd"] > 0


# ---- (b) no paid call and no usage row in the replace paths ----


def test_preview_apply_and_keep_make_no_provider_call(api, db, fake, world):
    set_ceiling(api, 5)
    before = usage_count(db)
    seen: list[str] = []
    seen += statements(lambda: replace(api, world))
    keep = []
    seen += statements(lambda: keep.append(api.post(f"/api/questions/{world['q']['q2']}/keep")))
    assert keep[0].status_code == 201, keep[0].text
    assert fake.calls == []
    assert [s for s in seen if "usage_event" in s] == []
    assert usage_count(db) == before


# ---- (c) a later-version extraction over the ceiling ----


def test_later_version_extraction_over_the_ceiling_fails_cleanly(api, db, fake, settings, world):
    v2 = replace(api, world)
    links = sorted(db.execute(select(TopicPassage.topic_id, TopicPassage.passage_id)).all())
    set_ceiling(api, 1)
    seed_usage(settings.effective_database_url, 1.0, started_at=datetime.now(ZONE))

    refused = api.post("/api/topics/extract", json={"document_version_id": v2})
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["estimate"]["kind"] == "topics.extract" and detail["estimate"]["fits"] is False
    job = db.scalar(select(AiJob).order_by(AiJob.id.desc()))
    db.refresh(job)
    assert (job.kind, job.status, job.error_message) == (
        "topics.extract",
        "failed",
        detail["message"],
    )
    assert job.document_version_id == v2
    assert api.get(f"/api/jobs/{job.id}").json()["resumable"] is True
    assert job.id not in [j["id"] for j in api.get("/api/jobs?live=true").json()]
    assert fake.calls == []
    assert sorted(db.execute(select(TopicPassage.topic_id, TopicPassage.passage_id)).all()) == links

    # Nothing is stuck: the subject is not busy, so a preview of another version works...
    preview = api.post(
        f"/api/documents/{world['a']}/versions/preview",
        files={"file": ("demokracja-3.txt", document_text([*V2, paragraph(10)]), "text/plain")},
    )
    assert preview.status_code == 200, preview.text
    api.delete(f"/api/documents/{world['a']}/versions/{preview.json()['token']}")
    # ...and once the ceiling allows it the job resumes and sends only the new passage.
    set_ceiling(api, 500)
    resumed = api.post(f"/api/jobs/{job.id}/resume")
    assert resumed.status_code == 202, resumed.text
    db.refresh(job)
    assert job.status == "done"
    assert len([c for c in fake.calls if c["task"] == "topics.extract_section"]) == 1

    # A refused job of a version that is replaced meanwhile cannot be resumed against it.
    set_ceiling(api, 1)
    v3 = replace(api, world, [*V2, paragraph(10)])
    refused = api.post("/api/topics/extract", json={"document_version_id": v3})
    assert refused.status_code == 409
    stale = db.scalar(select(AiJob).order_by(AiJob.id.desc()))
    replace(api, world, [*V2, paragraph(10), paragraph(11)])
    set_ceiling(api, 500)
    again = api.post(f"/api/jobs/{stale.id}/resume")
    assert again.status_code == 409 and again.json()["detail"] == VERSION_REPLACED
