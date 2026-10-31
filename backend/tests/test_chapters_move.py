import pytest
from sqlalchemy import select
from test_chapters_support import add_job, books_world
from test_topics_support import make_subject, make_version, upgrade

from app.db.session import SessionLocal
from app.features.library.models import Chapter
from app.features.topics.models import Topic

JOBS_RUNNING = "Wait for the topic or question work of this subject to finish first."
MOVE_TARGET = "Choose a chapter or enter a new chapter name."
FOREIGN = "This chapter belongs to another subject."


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def move(client, document_id: int, **body):
    return client.post(f"/api/documents/{document_id}/chapter", json=body)


def chapter_of(db, topic_id: int) -> int:
    db.expire_all()
    return db.get(Topic, topic_id).chapter_id


def test_move_to_new_chapter_takes_its_topics(client, db):
    w = books_world(db)
    response = move(client, w["B"].document_id, new_chapter_name="Rewolucja francuska")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"document_id", "chapter_id", "topics_moved"}
    new = body["chapter_id"]
    assert new != w["default"] and body["document_id"] == w["B"].document_id
    assert body["topics_moved"] == [w["TB1"].id]
    assert chapter_of(db, w["TR"].id) == new
    for key in ("TA1", "TA2", "TN", "TM"):
        assert chapter_of(db, w[key].id) == w["default"]
    name = db.scalars(select(Chapter.name).where(Chapter.id == new)).one()
    assert name == "Rewolucja francuska"
    questions = client.get(
        "/api/questions", params={"subject_id": w["subject"].id, "chapter_id": new}
    ).json()
    assert sorted(q["version_id"] for q in questions) == sorted(q.id for q in w["questions_TB1"])
    in_default = client.get(
        "/api/questions", params={"subject_id": w["subject"].id, "chapter_id": w["default"]}
    ).json()
    assert {q["primary_topic_id"] for q in in_default} == {w["TM"].id}
    listed = client.get(f"/api/subjects/{w['subject'].id}/documents").json()
    assert {d["id"]: d["chapter_id"] for d in listed} == {
        w["A"].document_id: w["default"],
        w["B"].document_id: new,
    }


def test_move_back_reunites(client, db):
    w = books_world(db)
    new = move(client, w["B"].document_id, new_chapter_name="Rewolucja").json()["chapter_id"]
    back = move(client, w["B"].document_id, chapter_id=w["default"])
    assert back.status_code == 200, back.text
    assert back.json()["topics_moved"] == [w["TB1"].id]
    assert chapter_of(db, w["TB1"].id) == w["default"]
    assert chapter_of(db, w["TR"].id) == w["default"]
    assert chapter_of(db, w["TM"].id) == w["default"]
    assert new != w["default"]


def test_move_refused_while_jobs_run(client, db):
    w = books_world(db)
    other = make_subject(db, "Biologia")
    other_version, _ = make_version(db, other.id, count=2, excluded=())
    foreign_job = add_job(db, "topics.extract", other.id, other_version.id, "running")
    assert move(client, w["B"].document_id, new_chapter_name="Rewolucja").status_code == 200
    foreign_job.status = "done"
    db.commit()

    live = add_job(db, "topics.extract", w["subject"].id, w["A"].id, "running")
    refused = move(client, w["B"].document_id, chapter_id=w["default"])
    assert refused.status_code == 409 and refused.json()["detail"] == JOBS_RUNNING
    live.status = "done"
    db.commit()
    generating = add_job(db, "questions.generate", w["subject"].id, None, "queued")
    refused = move(client, w["B"].document_id, chapter_id=w["default"])
    assert refused.status_code == 409 and refused.json()["detail"] == JOBS_RUNNING
    generating.status = "failed"
    db.commit()
    assert move(client, w["B"].document_id, chapter_id=w["default"]).status_code == 200


def test_move_to_existing_chapter_and_validation(client, db):
    w = books_world(db)
    other = make_subject(db, "Biologia")
    db.commit()
    target = client.post(
        f"/api/subjects/{w['subject'].id}/chapters", json={"name": "Rewolucja"}
    ).json()["id"]
    moved = move(client, w["B"].document_id, chapter_id=target)
    assert moved.status_code == 200 and moved.json()["topics_moved"] == [w["TB1"].id]
    for body in ({}, {"chapter_id": target, "new_chapter_name": "X"}, {"new_chapter_name": " "}):
        response = move(client, w["B"].document_id, **body)
        assert response.status_code == 422 and response.json()["detail"] == MOVE_TARGET
    foreign = db.scalars(select(Chapter.id).where(Chapter.subject_id == other.id)).one()
    response = move(client, w["B"].document_id, chapter_id=foreign)
    assert response.status_code == 422 and response.json()["detail"] == FOREIGN
    same = move(client, w["B"].document_id, chapter_id=target)
    assert same.status_code == 200 and same.json()["topics_moved"] == []
    assert move(client, 99999, chapter_id=target).status_code == 404
    existing_name = move(client, w["A"].document_id, new_chapter_name="rewolucja")
    assert existing_name.status_code == 200
    assert existing_name.json()["chapter_id"] == target
