import json

import pytest
from sqlalchemy import select
from test_doc_versions_questions import replace
from test_doc_versions_support import cited_question, replace_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.questions import service
from app.features.questions.models import RubricVersion
from app.features.rounds.models import RoundItem


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db, settings) -> dict:
    return replace_world(db, settings)


def add_issue(db, version_id: int, kind: str, passage_ids: list[int]) -> None:
    row = db.scalar(select(RubricVersion).where(RubricVersion.question_version_id == version_id))
    rubric = json.loads(row.rubric_json)
    rubric["source_issues"] = [{"kind": kind, "passage_ids": passage_ids, "note": "Sprzeczność."}]
    row.rubric_json = json.dumps(rubric)
    db.commit()


def held(db, world, text="Kto rządził?", note="Źródła się wykluczają.", issue=True):
    p = world["p"]
    q = cited_question(
        db, world["subject"], world["topics"]["t1"], text, (p[1],),
        status="needs_review", note=note,
    )  # fmt: skip
    if issue:
        add_issue(db, q.id, "contradiction", [p[1], p[2]])
    return q.id


def checked(client, version_id: int):
    return client.post(f"/api/questions/{version_id}/checked")


def test_checked_lifts_the_hold_in_place(client, db, world):
    vid = held(db, world)
    before = client.get(f"/api/questions/{vid}").json()
    assert before["status"] == "needs_review" and before["rubric"]["source_issues"]
    review = client.put(f"/api/questions/{vid}/review", json={"verdict": "ok", "note": None})
    assert review.status_code == 200, review.text
    response = checked(client, vid)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["version_id"], body["version"]) == (vid, before["version"])
    assert (body["status"], body["review_note"]) == ("active", None)
    assert body["text"] == before["text"]
    assert body["rubric"]["source_issues"] == before["rubric"]["source_issues"]
    listed = client.get(f"/api/questions?subject_id={world['subject']}").json()
    assert vid in [item["version_id"] for item in listed]
    reviews = client.get(f"/api/questions/reviews?subject_id={world['subject']}").json()
    assert [r["verdict"] for r in reviews if r["question_version_id"] == vid] == ["ok"]


def test_checked_after_failed_verification(client, db, world):
    vid = held(db, world, note=service.DEFAULT_REVIEW_NOTE, issue=False)
    body = checked(client, vid).json()
    assert (body["status"], body["review_note"]) == ("active", None)


def test_checked_question_is_selectable(client, db, world):
    vid = held(db, world)
    assert checked(client, vid).status_code == 200
    config = {"question_count": 5, "scope": {"kind": "subject"}}
    started = client.post("/api/rounds", json={"subject_id": world["subject"], "config": config})
    assert started.status_code == 201, started.text
    db.expire_all()
    asked = set(
        db.scalars(
            select(RoundItem.question_version_id).where(RoundItem.round_id == started.json()["id"])
        )
    )
    assert vid in asked


def test_checked_refusals(client, db, world):
    q = world["q"]
    assert checked(client, q["q1"]).json()["detail"] == service.NOT_IN_REVIEW
    assert checked(client, q["q1"]).status_code == 409
    assert checked(client, 999999).status_code == 404
    assert checked(client, 999999).json()["detail"] == service.QUESTION_NOT_FOUND

    vid = held(db, world)
    edited = client.put(f"/api/questions/{vid}", json={"text": "Kto rządził Atenami?"})
    assert edited.status_code == 200, edited.text
    old = checked(client, vid)
    assert (old.status_code, old.json()["detail"]) == (409, service.EDIT_LATEST)

    other = held(db, world, text="Inne pytanie?")
    assert client.post(f"/api/questions/{other}/retire").status_code == 200
    retired = checked(client, other)
    assert (retired.status_code, retired.json()["detail"]) == (409, service.EDIT_RETIRED)


def test_checked_refuses_a_document_replace_review(client, db, world):
    replace(client, world)
    q2 = world["q"]["q2"]
    assert client.get(f"/api/questions/{q2}").json()["status"] == "needs_review"
    refused = checked(client, q2)
    assert (refused.status_code, refused.json()["detail"]) == (409, service.CHECK_USE_KEEP)
    assert client.get(f"/api/questions/{q2}").json()["status"] == "needs_review"
    assert client.post(f"/api/questions/{q2}/keep").status_code == 201


def test_checked_after_a_replace_that_left_the_cited_text_unchanged(client, db, world):
    # P2 and P3 move to version 2 with the same text: the card has no source changes and offers
    # only "Mark as checked", so the backend must accept it.
    q = held(db, world)
    replace(client, world)
    before = client.get(f"/api/questions/{q}").json()
    assert (before["status"], before["source_changes"]) == ("needs_review", [])
    response = checked(client, q)
    assert response.status_code == 200, response.text
    assert (response.json()["version_id"], response.json()["status"]) == (q, "active")


def test_checked_refuses_a_removed_passage(client, db, world):
    replace(client, world)
    q3 = world["q"]["q3"]
    refused = checked(client, q3)
    assert (refused.status_code, refused.json()["detail"]) == (409, service.KEEP_REMOVED)
    assert client.get(f"/api/questions/{q3}").json()["status"] == "needs_review"


def test_edit_still_reactivates(client, db, world):
    vid = held(db, world)
    edited = client.put(f"/api/questions/{vid}", json={"text": "Kto rządził Atenami?"})
    assert edited.status_code == 200, edited.text
    assert (edited.json()["status"], edited.json()["version"]) == ("active", 2)


def test_review_count_ignores_source_issue_checks(client, db, world):
    count = lambda: client.get(f"/api/subjects/{world['subject']}").json()["source_review_count"]  # noqa: E731
    vid = held(db, world)
    assert count() == 0
    assert checked(client, vid).status_code == 200
    assert count() == 0
