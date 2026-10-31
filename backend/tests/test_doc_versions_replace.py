import os
import time

import pytest
from sqlalchemy import select
from test_chapters_support import add_job
from test_doc_versions_support import (
    V1,
    V2,
    document_text,
    paragraph,
    replace_world,
    statements,
    writes,
)
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.library.extraction import PassageDraft
from app.features.library.models import Document, SourcePassage
from app.features.library.versions import SOURCE_CHANGED, match_passages
from app.features.questions.models import QuestionVersion, RubricVersion
from app.features.topics.models import TopicPassage

IDENTICAL = "This file is identical to the latest version of the document."
JOBS_RUNNING = "Wait for the topic or question work of this subject to finish first."


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db, settings) -> dict:
    return replace_world(db, settings)


def staging(settings):
    return settings.data_dir / "staging" / "versions"


def preview(client, document_id: int, data: bytes, filename: str = "demokracja-2.txt"):
    return client.post(
        f"/api/documents/{document_id}/versions/preview",
        files={"file": (filename, data, "text/plain")},
    )


def apply(client, document_id: int, token: str, fingerprint: str):
    return client.post(
        f"/api/documents/{document_id}/versions/{token}/apply", json={"fingerprint": fingerprint}
    )


def replace(client, world, paragraphs=V2) -> dict:
    change = preview(client, world["a"], document_text(paragraphs))
    assert change.status_code == 200, change.text
    body = change.json()
    applied = apply(client, world["a"], body["token"], body["fingerprint"])
    assert applied.status_code == 201, applied.text
    return applied.json()


def passages_of(db, document_id: int, version: int) -> list[SourcePassage]:
    document = db.get(Document, document_id)
    return list(next(v for v in document.versions if v.version == version).passages)


def links(db, topic_id: int) -> set[int]:
    return set(db.scalars(select(TopicPassage.passage_id).where(TopicPassage.topic_id == topic_id)))


def questions_state(db) -> dict:
    db.expire_all()
    return {
        q.id: (q.status, q.review_note, q.updated_at, q.text)
        for q in db.scalars(select(QuestionVersion))
    }


def rubrics(db) -> dict:
    return {r.id: r.rubric_json for r in db.scalars(select(RubricVersion))}


class _Old:
    def __init__(self, pid: int, ordinal: int, text: str):
        self.id, self.ordinal, self.text = pid, ordinal, text


def _draft(n: int, text: str) -> PassageDraft:
    return PassageDraft(n, None, "l", text, 0, len(text))


def test_match_passages():
    old = [_Old(100 + n, n, t) for n, t in enumerate(V1)]
    new = [_draft(n, t) for n, t in enumerate(V2)]
    match = match_passages(old, new)
    assert sorted(pid for pid, _ in match.unchanged) == [100, 101, 102, 103, 105, 106]
    assert match.changed == [(104, 4)]
    assert match.removed == [107]
    assert match.new == [6]
    assert dict(match.unchanged)[106] == 7

    decomposed = "Zażółć".replace("ż", "ż")
    spaced = [_draft(0, "  Ateny\n\n i   polis "), _draft(1, decomposed)]
    match = match_passages([_Old(1, 0, "Ateny i polis"), _Old(2, 1, "Zażółć")], spaced)
    assert match.unchanged == [(1, 0), (2, 1)] and not (match.changed or match.removed)

    swapped = match_passages(
        [_Old(1, 0, "Pierwszy"), _Old(2, 1, "Drugi")], [_draft(0, "Drugi"), _draft(1, "Pierwszy")]
    )
    assert len(swapped.unchanged) == 1 and len(swapped.removed) == 1 and len(swapped.new) == 1
    assert not swapped.changed


def test_preview_counts_and_writes_nothing(client, db, settings, world):
    responses = []
    seen = statements(lambda: responses.append(preview(client, world["a"], document_text(V2))))
    response = responses[0]
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["unchanged"], body["changed"], body["removed"], body["new"]) == (6, 1, 1, 1)
    assert (body["exclusions_carried"], body["exclusions_total"]) == (1, 1)
    assert body["questions_to_review"] == 3
    assert body["topics_without_material"] == 1
    assert (body["current_version"], body["new_version"]) == (1, 2)
    assert body["passage_count"] == 8 and body["status"] == "imported"
    assert body["filename"] == "demokracja-2.txt" and len(body["fingerprint"]) == 16
    assert writes(seen) == []
    folders = list(staging(settings).iterdir())
    assert [f.name for f in folders] == [body["token"]]
    assert sorted(p.name for p in folders[0].iterdir()) == ["meta.json", "upload.txt"]
    db.expire_all()
    assert len(db.get(Document, world["a"]).versions) == 1


def test_preview_refusals(client, db, settings, world):
    identical = preview(client, world["a"], document_text(V1))
    assert identical.status_code == 409
    assert identical.json()["detail"] == IDENTICAL
    assert preview(client, world["a"], b"x" * (25 * 1024 * 1024 + 1)).status_code == 413
    assert preview(client, 9999, document_text(V2)).status_code == 404
    version_id = passages_of(db, world["b"], 1)[0].document_version_id
    add_job(db, "topics.extract", world["subject"], version_id, "running")
    busy = preview(client, world["a"], document_text(V2))
    assert busy.status_code == 409
    assert busy.json()["detail"] == JOBS_RUNNING
    assert not staging(settings).exists() or not list(staging(settings).iterdir())


def test_apply_carries_over_and_marks(client, db, settings, world):
    before_questions = questions_state(db)
    before_rubrics = rubrics(db)
    change = preview(client, world["a"], document_text(V2)).json()
    applied = apply(client, world["a"], change["token"], change["fingerprint"])
    assert applied.status_code == 201, applied.text
    body = applied.json()
    assert (body["questions_to_review"], body["topics_relinked"]) == (3, 2)
    assert body["document"]["version_count"] == 2
    db.expire_all()
    assert body["document"]["chapter_id"] == db.get(Document, world["a"]).chapter_id

    p = world["p"]
    v2 = passages_of(db, world["a"], 2)
    assert [x.previous_id for x in v2] == [p[0], p[1], p[2], p[3], p[4], p[5], None, p[6]]
    assert [x.excluded for x in v2] == [False] * 7 + [True]
    assert links(db, world["topics"]["t1"]) == {v2[0].id, v2[1].id, v2[2].id}
    assert links(db, world["topics"]["t2"]) == {v2[3].id, v2[4].id, v2[5].id}
    assert links(db, world["topics"]["t3"]) == set()
    assert not set(db.scalars(select(TopicPassage.passage_id))) & set(p)
    assert links(db, world["topics"]["tb"]) == set(world["pb"])

    after = questions_state(db)
    note = SOURCE_CHANGED.format(title=world["title"], version=2)
    assert note == "source changed: „Demokracja w Atenach” version 2"
    q = world["q"]
    for key in ("q2", "q3"):
        assert after[q[key]][:2] == ("needs_review", note)
    assert after[q["q5"]][:2] == ("needs_review", f"weryfikacja; {note}")
    for key in ("q1", "q4", "qb"):
        assert after[q[key]] == before_questions[q[key]]
    assert {k: v[3] for k, v in after.items()} == {k: v[3] for k, v in before_questions.items()}
    assert rubrics(db) == before_rubrics
    assert len(passages_of(db, world["a"], 1)) == 8
    assert not (staging(settings) / change["token"]).exists()
    folder = f"subject-{world['subject']}/document-{world['a']}"
    stored = settings.data_dir / "documents" / folder
    assert sorted(x.name for x in stored.iterdir()) == ["v1.txt", "v2.txt"]


def test_stale_fingerprint_409(client, db, settings, world):
    change = preview(client, world["a"], document_text(V2)).json()
    question = client.get(f"/api/questions/{world['q']['q2']}").json()
    edited = client.put(
        f"/api/questions/{world['q']['q2']}",
        json={"text": "Omów reformy Klejstenesa w Atenach.", "rubric": question["rubric"]},
    )
    assert edited.status_code == 200, edited.text
    before = questions_state(db)
    stale = apply(client, world["a"], change["token"], change["fingerprint"])
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["message"] == (
        "The document or its questions changed since you looked. Check the new summary."
    )
    fresh = detail["preview"]
    assert fresh["fingerprint"] != change["fingerprint"]
    assert fresh["questions_to_review"] == 3 and fresh["token"] == change["token"]
    assert questions_state(db) == before
    db.expire_all()
    assert len(db.get(Document, world["a"]).versions) == 1
    assert apply(client, world["a"], change["token"], fresh["fingerprint"]).status_code == 201


def test_discard_and_expiry(client, db, settings, world):
    change = preview(client, world["a"], document_text(V2)).json()
    gone = client.delete(f"/api/documents/{world['a']}/versions/{change['token']}")
    assert gone.status_code == 204
    assert not (staging(settings) / change["token"]).exists()
    expired = apply(client, world["a"], change["token"], change["fingerprint"])
    assert expired.status_code == 404
    assert expired.json()["detail"] == "This upload expired. Choose the file again."
    assert apply(client, world["a"], "not-a-token", "x").status_code == 404

    old = preview(client, world["a"], document_text(V2)).json()
    folder = staging(settings) / old["token"]
    stamp = time.time() - 25 * 3600
    for path in [*folder.iterdir(), folder]:
        os.utime(path, (stamp, stamp))
    fresh = preview(client, world["a"], document_text(V2)).json()
    assert not folder.exists()
    assert (staging(settings) / fresh["token"]).exists()


def test_one_step_upload_does_the_same(client, db, settings, world):
    response = client.post(
        f"/api/documents/{world['a']}/versions",
        files={"file": ("demokracja-2.txt", document_text(V2), "text/plain")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["version_count"] == 2
    db.expire_all()
    v2 = passages_of(db, world["a"], 2)
    assert [x.previous_id is None for x in v2] == [False] * 6 + [True, False]
    assert links(db, world["topics"]["t2"]) == {v2[3].id, v2[4].id, v2[5].id}
    assert links(db, world["topics"]["t3"]) == set()
    state = questions_state(db)
    q = world["q"]
    assert [state[q[k]][0] for k in ("q1", "q2", "q3", "q4", "q5", "qb")] == [
        "active", "needs_review", "needs_review", "active", "needs_review", "active"
    ]  # fmt: skip


def test_third_version_chains(client, db, settings, world):
    replace(client, world)
    v3 = [paragraph(1, "oligarchii"), *V2[1:]]
    change = preview(client, world["a"], document_text(v3)).json()
    assert (change["changed"], change["unchanged"]) == (1, 7)
    assert change["questions_to_review"] == 1  # Q2 still cites the version 1 P1
    applied = apply(client, world["a"], change["token"], change["fingerprint"])
    assert applied.status_code == 201, applied.text
    db.expire_all()
    v2 = passages_of(db, world["a"], 2)
    assert passages_of(db, world["a"], 3)[0].previous_id == v2[0].id
    note = SOURCE_CHANGED.format(title=world["title"], version=3)
    assert questions_state(db)[world["q"]["q2"]][1].endswith(note)
