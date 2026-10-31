import pytest
from test_topics_support import make_question, make_subject, make_topic, make_version, upgrade

from app.db.session import SessionLocal
from app.features.questions.models import QuestionFamily, QuestionVersion
from app.features.topics import service
from app.features.topics.models import LearningObjective, Term, Topic, TopicPassage


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
    b = make_topic(db, subject.id, "Sparta", passages[2:4], ("Cel B1",), ("Helota",), 1)
    c = make_topic(db, subject.id, "Persja", passages[4:], ("Cel C1",), (), 2)
    return subject, passages, a, b, c


def fresh(db, model, id_):
    db.expire_all()
    return db.get(model, id_)


def test_rename(db, client, world):
    _, _, a, _, _ = world
    response = client.patch(f"/api/topics/{a.id}", json={"title": "Ateny klasyczne"})
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Ateny klasyczne"
    topic = fresh(db, Topic, a.id)
    assert topic.title == "Ateny klasyczne" and topic.origin == "user"
    response = client.patch(f"/api/topics/{a.id}", json={"description": "Opis"})
    assert fresh(db, Topic, a.id).description == "Opis"
    assert client.patch(f"/api/topics/{a.id}", json={"title": "  "}).status_code == 422
    assert client.patch("/api/topics/99999", json={"title": "X"}).json()["detail"] == (
        "Topic not found."
    )


def test_merge_moves_everything_and_repoints_questions(db, client, world):
    subject, passages, a, b, c = world
    question = make_question(db, subject.id, b.id)
    response = client.post("/api/topics/merge", json={"topic_ids": [a.id, b.id], "title": "Grecja"})
    assert response.status_code == 200, response.text
    merged_id = response.json()["id"]
    merged = fresh(db, Topic, merged_id)
    assert merged.origin == "user" and merged.status == "active" and merged.title == "Grecja"
    assert fresh(db, Topic, a.id).status == "removed"
    assert fresh(db, Topic, b.id).status == "removed"
    objectives = db.query(LearningObjective).filter_by(topic_id=merged_id).all()
    assert sorted(o.text for o in objectives) == ["Cel A1", "Cel A2", "Cel B1"]
    links = {link.passage_id for link in db.query(TopicPassage).filter_by(topic_id=merged_id)}
    assert links == {p.id for p in passages[:4]}
    assert {t.topic_id for t in db.query(Term)} == {merged_id}
    family = fresh(db, QuestionFamily, db.get(QuestionVersion, question.id).family_id)
    assert family.primary_topic_id == merged_id
    assert response.json()["question_count"] == 1
    bad = client.post("/api/topics/merge", json={"topic_ids": [a.id], "title": "X"})
    assert bad.status_code == 422


def test_split_requires_full_assignment(db, client, world):
    _, passages, a, _, _ = world
    objectives = db.query(LearningObjective).filter_by(topic_id=a.id).order_by("ordinal").all()
    incomplete = {
        "parts": [
            {
                "title": "Część 1",
                "objective_ids": [objectives[0].id],
                "passage_ids": [passages[0].id],
            },
            {"title": "Część 2", "objective_ids": [], "passage_ids": [passages[1].id]},
        ]
    }
    response = client.post(f"/api/topics/{a.id}/split", json=incomplete)
    assert response.status_code == 422
    assert response.json()["detail"] == "Every objective and passage must go to exactly one part."
    twice = {
        "parts": [
            {"title": "Część 1", "objective_ids": [objectives[0].id, objectives[1].id],
             "passage_ids": [passages[0].id]},
            {"title": "Część 2", "objective_ids": [objectives[1].id],
             "passage_ids": [passages[1].id]},
        ]
    }  # fmt: skip
    assert client.post(f"/api/topics/{a.id}/split", json=twice).status_code == 422
    foreign = {
        "parts": [
            {"title": "Część 1", "objective_ids": [objectives[0].id, objectives[1].id],
             "passage_ids": [passages[0].id, passages[5].id]},
            {"title": "Część 2", "objective_ids": [], "passage_ids": [passages[1].id]},
        ]
    }  # fmt: skip
    assert client.post(f"/api/topics/{a.id}/split", json=foreign).status_code == 422
    assert fresh(db, Topic, a.id).status == "active"


def test_split_marks_questions_needs_review(db, client, world):
    subject, passages, a, _, _ = world
    question = make_question(db, subject.id, a.id)
    objectives = db.query(LearningObjective).filter_by(topic_id=a.id).order_by("ordinal").all()
    body = {
        "parts": [
            {
                "title": "Część 1",
                "objective_ids": [objectives[0].id],
                "passage_ids": [passages[0].id],
            },
            {
                "title": "Część 2",
                "objective_ids": [objectives[1].id],
                "passage_ids": [passages[1].id],
            },
        ]
    }
    response = client.post(f"/api/topics/{a.id}/split", json=body)
    assert response.status_code == 200, response.text
    parts = response.json()
    assert [p["title"] for p in parts] == ["Część 1", "Część 2"]
    assert [p["passage_count"] for p in parts] == [1, 1]
    assert [[o["text"] for o in p["objectives"]] for p in parts] == [["Cel A1"], ["Cel A2"]]
    assert fresh(db, Topic, a.id).status == "removed"
    version = fresh(db, QuestionVersion, question.id)
    assert version.status == "needs_review"
    assert version.review_note == "topic split"
    family = db.get(QuestionFamily, version.family_id)
    assert family.primary_topic_id == parts[0]["id"]
    assert {t.term for t in db.query(Term).filter_by(topic_id=parts[0]["id"])} == {"Polis"}


def test_delete_retires_questions_and_returns_counts(db, client, world):
    subject, _, _, b, _ = world
    active = make_question(db, subject.id, b.id)
    make_question(db, subject.id, b.id, text="Stare pytanie o Spartę.", status="superseded")
    response = client.delete(f"/api/topics/{b.id}")
    assert response.status_code == 200, response.text
    assert response.json() == {"topic_id": b.id, "questions_retired": 1}
    assert fresh(db, Topic, b.id).status == "removed"
    assert fresh(db, QuestionVersion, active.id).status == "retired"
    assert client.delete(f"/api/topics/{b.id}").status_code == 409


def test_removed_topics_hidden_by_default(db, client, world):
    subject, _, _, b, _ = world
    client.delete(f"/api/topics/{b.id}")
    titles = [t["title"] for t in client.get(f"/api/topics?subject_id={subject.id}").json()]
    assert titles == ["Ateny", "Persja"]
    every = client.get(f"/api/topics?subject_id={subject.id}&include_removed=true").json()
    assert [t["title"] for t in every] == ["Ateny", "Sparta", "Persja"]
    assert every[1]["status"] == "removed"


def test_glossary_active_topics_capped(db, client, world):
    subject, _, a, b, _ = world
    assert service.get_glossary(db, subject.id) == ["Polis", "Helota"]
    assert service.get_glossary(db, subject.id, topic_ids=[b.id]) == ["Helota"]
    for n in range(70):
        db.add(Term(subject_id=subject.id, topic_id=a.id, term=f"T{n}", term_key=f"t{n}"))
    db.commit()
    assert len(service.get_glossary(db, subject.id)) == 60
    assert len(service.get_glossary(db, subject.id, limit=5)) == 5
    client.delete(f"/api/topics/{b.id}")
    assert "Helota" not in service.get_glossary(db, subject.id, limit=100)
