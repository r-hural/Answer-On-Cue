import json

import pytest
from test_topics_support import (
    make_question,
    make_subject,
    make_topic,
    make_version,
    upgrade,
)

from app.db.session import SessionLocal
from app.features.questions import service
from app.features.questions.models import QuestionVersion, RubricVersion
from app.features.questions.schemas import QuestionOut, QuestionWithRubric


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    subject = make_subject(db)
    _, passages = make_version(db, subject.id, count=4, excluded=())
    a = make_topic(db, subject.id, "Ateny", passages[:2], ("Wyjaśnić demokrację",), (), 0)
    b = make_topic(db, subject.id, "Sparta", passages[2:], ("Opisać Spartę",), (), 1)
    ids = (passages[0].id, passages[1].id)
    qa = make_question(db, subject.id, a.id, "Omów demokrację ateńską.", passage_ids=ids)
    qb = make_question(db, subject.id, b.id, "Kim byli heloci?", passage_ids=(passages[2].id,))
    qr = make_question(
        db, subject.id, b.id, "Jak wychowywano Spartan?", "needs_review", (passages[3].id,)
    )
    return subject, passages, a, b, qa, qb, qr


def has_key(value, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(has_key(v, key) for v in value.values())
    if isinstance(value, list):
        return any(has_key(v, key) for v in value)
    return False


def test_list_without_rubric(client, world):
    subject, *_ = world
    body = client.get("/api/questions", params={"subject_id": subject.id}).json()
    assert len(body) == 2  # active only by default
    assert not has_key(body, "rubric")
    assert not has_key(body, "model_answer")
    assert set(body[0]) == set(QuestionOut.model_fields)
    flagged = client.get(
        "/api/questions", params={"subject_id": subject.id, "status": "needs_review"}
    ).json()
    assert [q["text"] for q in flagged] == ["Jak wychowywano Spartan?"]


def test_get_with_rubric_and_passage_refs(client, world):
    _, passages, a, _, qa, _, _ = world
    body = client.get(f"/api/questions/{qa.id}").json()
    assert set(QuestionWithRubric.model_fields) <= set(body)
    assert body["primary_topic_id"] == a.id
    assert [p["text"] for p in body["rubric"]["essential_points"]] == [
        "Zgromadzenie ludowe",
        "Rada Pięciuset",
    ]
    assert [p["id"] for p in body["passages"]] == [passages[0].id, passages[1].id]
    assert body["passages"][0]["locator"] == "sekcja 1"
    assert set(body["passages"][0]) == {"id", "locator", "heading", "text"}


def test_edit_creates_new_version_and_supersedes(db, client, world):
    _, passages, _, _, qa, _, _ = world
    old_rubric = client.get(f"/api/questions/{qa.id}").json()["rubric"]
    old_rubric["essential_points"][0]["weight"] = 3
    old_rubric["essential_points"][1]["weight"] = 1
    response = client.put(
        f"/api/questions/{qa.id}",
        json={"text": "Omów ustrój demokracji ateńskiej.", "style": "short", "rubric": old_rubric},
    )
    assert response.status_code == 200, response.text
    new = response.json()
    assert new["version"] == 2 and new["family_id"] == qa.family_id
    assert new["status"] == "active" and new["style"] == "short"
    assert [p["weight"] for p in new["rubric"]["essential_points"]] == [0.75, 0.25]
    db.expire_all()
    old = db.get(QuestionVersion, qa.id)
    assert old.status == "superseded"
    assert old.text == "Omów demokrację ateńską."  # history is not rewritten
    old_body = client.get(f"/api/questions/{qa.id}").json()
    assert old_body["rubric"]["essential_points"][0]["weight"] == 0.5
    new_row = db.get(QuestionVersion, new["version_id"])
    assert json.loads(new_row.provenance_json)["origin"] == "user"
    assert db.query(RubricVersion).filter_by(question_version_id=new_row.id).count() == 1
    # text-only edit keeps the rubric
    third = client.put(f"/api/questions/{new['version_id']}", json={"difficulty": "analysis"})
    assert third.json()["version"] == 3
    assert third.json()["rubric"] == new["rubric"]


def test_edit_validates_like_generation(client, world):
    _, passages, _, _, qa, _, _ = world
    assert client.put(f"/api/questions/{qa.id}", json={"text": "Krótko"}).status_code == 422
    rubric = client.get(f"/api/questions/{qa.id}").json()["rubric"]
    rubric["essential_points"][1]["passage_ids"] = [passages[3].id]  # another topic's passage
    response = client.put(f"/api/questions/{qa.id}", json={"rubric": rubric})
    assert response.status_code == 422
    assert "no source for a point" in response.json()["detail"]


def test_edit_superseded_409(client, world):
    _, _, _, _, qa, _, _ = world
    client.put(f"/api/questions/{qa.id}", json={"text": "Omów ustrój demokracji ateńskiej."})
    response = client.put(f"/api/questions/{qa.id}", json={"text": "Inna treść pytania o Ateny."})
    assert response.status_code == 409
    assert response.json()["detail"] == "Edit the latest version of the question."


def test_retire(db, client, world):
    _, _, _, _, _, qb, _ = world
    response = client.post(f"/api/questions/{qb.id}/retire")
    assert response.status_code == 200
    assert response.json()["status"] == "retired"
    db.expire_all()
    assert db.get(QuestionVersion, qb.id).status == "retired"


def test_unknown_ids_404(db, client):
    for response in (
        client.get("/api/questions/99999"),
        client.put("/api/questions/99999", json={"text": "Jakieś pytanie testowe."}),
        client.post("/api/questions/99999/retire"),
    ):
        assert response.status_code == 404
        assert response.json()["detail"] == "Question not found."


def test_list_questions_filters(db, world):
    subject, _, a, b, qa, qb, qr = world
    ids = lambda qs: [q.version_id for q in qs]  # noqa: E731
    assert ids(service.list_questions(db, subject.id)) == [qa.id, qb.id]
    assert ids(service.list_questions(db, subject.id, topic_ids=[b.id])) == [qb.id]
    assert ids(
        service.list_questions(
            db, subject.id, topic_ids=[b.id], statuses=("active", "needs_review")
        )
    ) == [qb.id, qr.id]
    assert service.list_questions(db, subject.id, topic_ids=[]) == []
    rubric_view = service.get_question(db, qa.id, with_rubric=True)
    assert isinstance(rubric_view, QuestionWithRubric)
    plain = service.get_question(db, qa.id, with_rubric=False)
    assert type(plain) is QuestionOut


def test_question_flagged_by_split_can_be_edited(db, client, world):
    _, passages, a, _, qa, _, _ = world

    objectives = client.get(f"/api/topics?subject_id={world[0].id}").json()[0]["objectives"]
    body = {
        "parts": [
            {"title": "Część 1", "objective_ids": [o["id"] for o in objectives],
             "passage_ids": [passages[0].id]},
            {"title": "Część 2", "objective_ids": [], "passage_ids": [passages[1].id]},
        ]
    }  # fmt: skip
    assert client.post(f"/api/topics/{a.id}/split", json=body).status_code == 200
    response = client.put(
        f"/api/questions/{qa.id}", json={"text": "Omów zasady demokracji w Atenach."}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"


def test_edit_retired_question_409(client, world):
    _, _, _, b, _, qb, _ = world
    client.delete(f"/api/topics/{b.id}")
    response = client.put(f"/api/questions/{qb.id}", json={"text": "Kim byli heloci w Sparcie?"})
    assert response.status_code == 409
    assert response.json()["detail"] == "This question has been retired."
