"""Document versions, edge cases: an unread file never replaces a version, carried-over
versions are marked explicitly, passages added in an unanalysed intermediate version are still
analysed and generated, a job of a replaced version cannot be resumed, the extraction estimate
of an older version is empty, and an edit refuses a removed citation wherever it is cited."""

import pytest
from alembic import command
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, select, text
from test_chapters_support import add_job
from test_doc_versions_schema import _old_world
from test_doc_versions_support import (
    BASE_REVISION,
    V2,
    cited_question,
    document_text,
    migration_config,
    paragraph,
    replace_world,
)
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.jobs import service as jobs
from app.features.jobs.models import AiJob
from app.features.library import versions
from app.features.library.extraction import FAILED_MESSAGE, TOO_LITTLE_TEXT_MESSAGE
from app.features.library.models import Document, DocumentVersion
from app.features.providers.fakes import FakeTextGenerator
from app.features.providers.registry import _generation_dep
from app.features.questions import service as questions
from app.features.questions.models import QuestionVersion
from app.features.questions.validation import REASON_REMOVED_PASSAGE
from app.features.topics import service as topics
from app.features.topics.models import TopicPassage

VERSION_REPLACED = "A newer version of this document exists. Use the latest version."
KEEP_REMOVED = (
    "This question cites a passage that is no longer in the document. Edit it to choose another "
    "passage, or retire it."
)
NO_CHANGED_TOPICS = (
    "No topic has new or changed passages in this version. Analyse its new passages first, "
    "or choose All topics."
)


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def api(client):
    """No provider call is expected: any call fails the fake."""
    client.app.dependency_overrides[_generation_dep] = lambda: FakeTextGenerator({})
    return client


@pytest.fixture
def world(db, settings) -> dict:
    return replace_world(db, settings)


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


def version_ids(db, document_id: int) -> list[int]:
    db.expire_all()
    return [v.id for v in db.get(Document, document_id).versions]


def snapshot(db, world) -> dict:
    db.expire_all()
    return {
        "versions": version_ids(db, world["a"]),
        "links": sorted(db.execute(select(TopicPassage.topic_id, TopicPassage.passage_id)).all()),
        "questions": sorted(
            db.execute(
                select(QuestionVersion.id, QuestionVersion.status, QuestionVersion.review_note)
            ).all()
        ),
    }


def done(db, kind: str, world, version_id: int) -> AiJob:
    return add_job(db, kind, world["subject"], version_id, "done")


# ---- a file that was not read never replaces a version ----

UNREAD = [
    ("demokracja-2.docx", b"PK\x03\x04 not really a zip", FAILED_MESSAGE),
    ("demokracja-2.pdf", b"%PDF-1.7\n1 0 obj << /Broken", FAILED_MESSAGE),
    ("demokracja-2.txt", "Za krótki tekst.".encode(), TOO_LITTLE_TEXT_MESSAGE),
]


@pytest.mark.parametrize(("filename", "data", "message"), UNREAD)
def test_unread_file_is_refused_by_preview_and_one_step(
    client, db, settings, world, filename, data, message
):
    before = snapshot(db, world)
    for path in (
        f"/api/documents/{world['a']}/versions/preview",
        f"/api/documents/{world['a']}/versions",
    ):
        response = client.post(path, files={"file": (filename, data, "application/octet-stream")})
        assert response.status_code == 422, (path, response.text)
        assert response.json()["detail"] == message
    staged = settings.data_dir / versions.STAGING_FOLDER
    assert not staged.exists() or list(staged.iterdir()) == []
    assert snapshot(db, world) == before


def test_apply_refuses_an_unread_staged_file(client, db, settings, world):
    before = snapshot(db, world)
    token = versions._stage(settings, world["a"], "demokracja-2.docx", ".docx", b"PK\x03\x04 junk")
    response = client.post(
        f"/api/documents/{world['a']}/versions/{token}/apply", json={"fingerprint": "0" * 16}
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == FAILED_MESSAGE
    assert snapshot(db, world) == before


# ---- carried-over versions are marked, not guessed ----


def test_carried_over_version_without_predecessors_still_tracks_removed_passages(client, db, world):
    v2 = replace(client, world)
    version = db.get(DocumentVersion, v2)
    assert version.carried_over is True
    # A version where no passage matched its predecessor (nothing records a predecessor) is a
    # carried-over version all the same: every cited passage of version 1 was removed.
    for passage in version.passages:
        passage.previous_id = None
    db.commit()

    q1 = world["q"]["q1"]
    body = client.get(f"/api/questions/{q1}").json()
    assert [(c["old"]["id"], c["new"], c["new_version"]) for c in body["source_changes"]] == [
        (world["p"][1], None, 2)
    ]
    keep = client.post(f"/api/questions/{q1}/keep")
    assert keep.status_code == 409 and keep.json()["detail"] == KEEP_REMOVED
    edit = client.put(
        f"/api/questions/{q1}", json={"text": "Nowe pytanie?", "rubric": body["rubric"]}
    )
    assert edit.status_code == 422 and REASON_REMOVED_PASSAGE in edit.json()["detail"]
    # The bank's notice counts the questions with source changes: all those citing document A.
    with_changes = [
        key
        for key, version_id in world["q"].items()
        if client.get(f"/api/questions/{version_id}").json()["source_changes"]
    ]
    assert sorted(with_changes) == ["q1", "q2", "q3", "q5"]


def test_a_version_not_carried_over_keeps_its_passages_current(client, db, world):
    v2 = replace(client, world)
    q2 = world["q"]["q2"]
    assert client.get(f"/api/questions/{q2}").json()["source_changes"] != []
    db.get(DocumentVersion, v2).carried_over = False  # as a version stored before carry-over
    db.commit()
    assert client.get(f"/api/questions/{q2}").json()["source_changes"] == []
    keep = client.post(f"/api/questions/{q2}/keep")
    assert keep.status_code == 409 and keep.json()["detail"] == questions.KEEP_NOT_NEEDED


def test_migration_marks_existing_versions_not_carried_over(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/m.db"
    cfg = migration_config(url)
    command.upgrade(cfg, BASE_REVISION)
    engine = create_engine(url)
    with engine.begin() as conn:
        _old_world(conn)
    command.upgrade(cfg, "head")
    column = {c["name"]: c for c in inspect(create_engine(url)).get_columns("document_version")}
    assert column["carried_over"]["nullable"] is False
    with engine.begin() as conn:
        flags = conn.execute(text("SELECT carried_over FROM document_version")).scalars().all()
    assert flags == [0, 0]
    command.downgrade(cfg, BASE_REVISION)
    columns = {c["name"] for c in inspect(create_engine(url)).get_columns("document_version")}
    assert "carried_over" not in columns
    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM document_version")).scalar() == 2
    command.upgrade(cfg, "head")


# ---- an unanalysed intermediate version ----


def test_extraction_sends_passages_added_in_an_unanalysed_version(client, db, world):
    done(db, "topics.extract", world, version_ids(db, world["a"])[0])
    v2 = replace(client, world)  # adds paragraph 9; not analysed
    added_in_v2 = next(p for p in db.get(DocumentVersion, v2).passages if p.previous_id is None)
    v3 = replace(client, world, [*V2, paragraph(10)])
    passages = db.get(DocumentVersion, v3).passages
    carried_9 = next(p.id for p in passages if p.previous_id == added_in_v2.id)
    added_10 = next(p.id for p in passages if p.previous_id is None)
    sections = topics.extraction_sections(db, db.get(DocumentVersion, v3))
    assert sorted(i for s in sections for i in s) == sorted([carried_9, added_10])

    # Once the intermediate version is analysed, only what came after it is sent.
    done(db, "topics.extract", world, v2)
    sections = topics.extraction_sections(db, db.get(DocumentVersion, v3))
    assert sections == [[added_10]]


def test_generation_covers_a_change_made_in_an_ungenerated_version(client, db, world):
    v1 = version_ids(db, world["a"])[0]
    done(db, "questions.generate", world, v1)
    replace(client, world)  # P5 changed in version 2; nothing generated for it
    v3 = replace(client, world, [*V2, paragraph(10)])  # P5 as in version 2
    subject = world["subject"]
    picked = questions.generation_topic_ids(db, subject, None, document_version_id=v3)
    assert picked == [world["topics"]["t2"]]

    # Generated for version 2: version 3 brings no change to any topic.
    done(db, "questions.generate", world, version_ids(db, world["a"])[1])
    with pytest.raises(HTTPException) as refused:
        questions.generation_topic_ids(db, subject, None, document_version_id=v3)
    assert refused.value.detail == NO_CHANGED_TOPICS


# ---- a resumable job of a replaced version ----


def test_job_of_a_replaced_version_cannot_be_resumed(api, db, world):
    client = api
    v1 = version_ids(db, world["a"])[0]
    params = {"document_version_id": v1, "sections": [[world["p"][0]]], "chapter_id": None}
    stale = add_job(db, "topics.extract", world["subject"], v1, "failed", params)
    paused = add_job(db, "questions.generate", world["subject"], v1, "partial", {"batches": []})
    v2 = replace(client, world)  # resumable jobs do not block a replace
    for job in (stale, paused):
        response = client.post(f"/api/jobs/{job.id}/resume")
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == VERSION_REPLACED
        db.refresh(job)
        assert job.status in ("failed", "partial")

    current = add_job(db, "topics.extract", world["subject"], v2, "failed", params)
    all_topics = add_job(db, "questions.generate", world["subject"], None, "failed", {})
    for job in (current, all_topics):
        assert jobs.prepare_resume(db, job.id).status == "queued"


# ---- the extraction estimate of an older version ----


def test_extraction_estimate_of_an_older_version_is_empty(api, db, world):
    client = api
    v1 = version_ids(db, world["a"])[0]
    before = client.get(f"/api/costs/estimate/extraction?document_version_id={v1}").json()
    assert before["sections"] > 0
    replace(client, world)
    after = client.get(f"/api/costs/estimate/extraction?document_version_id={v1}").json()
    assert (after["sections"], after["usd"]) == (0, 0)
    refused = client.post("/api/topics/extract", json={"document_version_id": v1})
    assert refused.status_code == 409 and refused.json()["detail"] == VERSION_REPLACED


# ---- a removed citation outside the essential points ----


def test_edit_refuses_a_removed_citation_in_enrichment_or_source_issue(client, db, world):
    p = world["p"]
    enriched = cited_question(
        db, world["subject"], world["topics"]["t2"], "Omów reformy.", (p[3],), (p[7],)
    )
    replace(client, world)  # P8 removed
    body = client.get(f"/api/questions/{enriched.id}").json()
    edit = client.put(f"/api/questions/{enriched.id}", json={"rubric": body["rubric"]})
    assert edit.status_code == 422, edit.text
    assert REASON_REMOVED_PASSAGE in edit.json()["detail"]

    q1 = client.get(f"/api/questions/{world['q']['q1']}").json()
    rubric = {
        **q1["rubric"],
        "source_issues": [{"kind": "unclear", "passage_ids": [p[7]], "note": "Niejasne."}],
    }
    edit = client.put(f"/api/questions/{world['q']['q1']}", json={"rubric": rubric})
    assert edit.status_code == 422, edit.text
    assert REASON_REMOVED_PASSAGE in edit.json()["detail"]

    # Without the removed citation the edit goes through, citing the successors.
    rubric = {**body["rubric"], "enrichment_points": []}
    edit = client.put(f"/api/questions/{enriched.id}", json={"rubric": rubric})
    assert edit.status_code == 200, edit.text
    cited = edit.json()["rubric"]["essential_points"][0]["passage_ids"]
    assert len(cited) == 1 and cited[0] != p[3]  # moved to its successor in version 2
