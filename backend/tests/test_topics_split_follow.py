import pytest
from test_topics_support import make_question, make_subject, make_topic, make_version, upgrade

from app.db.session import SessionLocal
from app.features.questions.models import QuestionFamily, QuestionVersion
from app.features.topics.models import LearningObjective, Topic


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    subject = make_subject(db)
    _, passages = make_version(db, subject.id, count=6, excluded=())
    a = make_topic(db, subject.id, "Ateny", passages[:2], ("Cel A1", "Cel A2"), ("Polis",), 0)
    return subject, passages, a


def _objectives(db, topic):
    return db.query(LearningObjective).filter_by(topic_id=topic.id).order_by("ordinal").all()


def test_split_without_passages_keeps_all_in_each_part(client, db, world):
    _, _, a = world
    o = _objectives(db, a)
    body = {
        "parts": [
            {"title": "Część 1", "objective_ids": [o[0].id], "passage_ids": []},
            {"title": "Część 2", "objective_ids": [o[1].id], "passage_ids": []},
        ]
    }
    response = client.post(f"/api/topics/{a.id}/split", json=body)
    assert response.status_code == 200, response.text
    assert [p["passage_count"] for p in response.json()] == [2, 2]


def test_split_with_partial_passages_is_rejected(client, db, world):
    _, passages, a = world
    o = _objectives(db, a)
    body = {
        "parts": [
            {"title": "Część 1", "objective_ids": [o[0].id], "passage_ids": [passages[0].id]},
            {"title": "Część 2", "objective_ids": [o[1].id], "passage_ids": []},
        ]
    }
    response = client.post(f"/api/topics/{a.id}/split", json=body)
    assert response.status_code == 422
    assert response.json()["detail"] == "Every objective and passage must go to exactly one part."


def test_questions_follow_their_objective(client, db, world):
    subject, _, a = world
    o = _objectives(db, a)
    versions = [make_question(db, subject.id, a.id) for _ in range(3)]
    versions[0].objective_id = o[0].id
    versions[1].objective_id = o[1].id
    versions[2].objective_id = None
    db.commit()
    ids = [v.id for v in versions]
    body = {
        "parts": [
            {"title": "Część 1", "objective_ids": [o[0].id], "passage_ids": []},
            {"title": "Część 2", "objective_ids": [o[1].id], "passage_ids": []},
        ]
    }
    parts = client.post(f"/api/topics/{a.id}/split", json=body).json()
    db.expire_all()
    homes = []
    for vid in ids:
        version = db.get(QuestionVersion, vid)
        assert version.status == "needs_review"
        assert version.review_note == "topic split"
        homes.append(db.get(QuestionFamily, version.family_id).primary_topic_id)
    assert homes == [parts[0]["id"], parts[1]["id"], parts[0]["id"]]
    assert db.get(Topic, a.id).status == "removed"
