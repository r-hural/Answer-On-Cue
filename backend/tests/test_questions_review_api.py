import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from test_topics_support import (
    make_question,
    make_subject,
    make_topic,
    make_version,
    upgrade,
)

from app.db.session import SessionLocal
from app.features.questions.models import QuestionReview


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db: Session):
    subject = make_subject(db)
    _, passages = make_version(db, subject.id, count=4, excluded=())
    topic = make_topic(db, subject.id, "Ateny", passages[:2], ("Wyjaśnić demokrację",), (), 0)
    ids = (passages[0].id, passages[1].id)
    first = make_question(db, subject.id, topic.id, "Omów demokrację ateńską.", passage_ids=ids)
    second = make_question(db, subject.id, topic.id, "Kim byli heloci?", passage_ids=ids)
    return subject, passages, topic, first, second


def put(client, version_id, verdict="ok", note=None):
    return client.put(
        f"/api/questions/{version_id}/review", json={"verdict": verdict, "note": note}
    )


def test_migration_creates_table_and_cascades(settings, db, world):
    subject, _, _, first, _ = world
    assert (
        "question_review"
        in inspect(create_engine(settings.effective_database_url)).get_table_names()
    )
    db.add(QuestionReview(question_version_id=first.id, verdict="ok"))
    db.commit()
    db.delete(subject)
    db.commit()
    assert db.query(QuestionReview).count() == 0


def test_put_review_upsert(client, db, world):
    first = world[3]
    response = put(client, first.id, "weak", "Za ogólne.")
    assert response.status_code == 200
    assert response.json()["verdict"] == "weak"
    response = put(client, first.id, "bad", "  ")
    assert response.json()["verdict"] == "bad"
    assert response.json()["note"] is None
    assert db.query(QuestionReview).count() == 1


def test_delete_review(client, db, world):
    first = world[3]
    put(client, first.id)
    assert client.delete(f"/api/questions/{first.id}/review").status_code == 204
    assert client.delete(f"/api/questions/{first.id}/review").status_code == 204
    assert db.query(QuestionReview).count() == 0


def test_unknown_version_404(client, world):
    assert put(client, 9999).status_code == 404


def test_note_too_long_422(client, world):
    assert put(client, world[3].id, "ok", "x" * 1001).status_code == 422
    assert put(client, world[3].id, "great").status_code == 422


def test_edit_creates_version_without_verdict(client, world):
    subject, _, _, first, _ = world
    put(client, first.id, "bad", "Źle.")
    edited = client.put(f"/api/questions/{first.id}", json={"text": "Omów ustrój Aten."})
    assert edited.status_code == 200
    new_id = edited.json()["version_id"]
    reviews = {
        r["question_version_id"]: r
        for r in client.get(f"/api/questions/reviews?subject_id={subject.id}").json()
    }
    assert set(reviews) == {first.id}  # the old verdict stays on the old version
    assert new_id not in reviews
    assert "Źle." in client.get(f"/api/questions/reviews/export?subject_id={subject.id}").text


def test_retire_keeps_verdict(client, world):
    subject, _, _, first, _ = world
    put(client, first.id, "weak")
    assert client.post(f"/api/questions/{first.id}/retire").status_code == 200
    reviews = client.get(f"/api/questions/reviews?subject_id={subject.id}").json()
    assert [r["question_version_id"] for r in reviews] == [first.id]


def test_reviews_list_by_subject(client, db, world):
    subject, _, _, first, second = world
    other = make_subject(db, "Fizyka")
    elsewhere = make_question(db, other.id, None, "Co to energia?")
    put(client, first.id, "ok")
    put(client, second.id, "bad", "Nie wynika z tekstu.")
    put(client, elsewhere.id, "ok")
    reviews = client.get(f"/api/questions/reviews?subject_id={subject.id}").json()
    assert [(r["question_version_id"], r["verdict"]) for r in reviews] == [
        (first.id, "ok"),
        (second.id, "bad"),
    ]
    assert client.get("/api/questions/reviews?subject_id=999").json() == []


def test_export_markdown(client, world):
    subject, passages, _, first, second = world
    put(client, first.id, "ok")
    put(client, second.id, "weak", "Brakuje punktu.")
    response = client.get(f"/api/questions/reviews/export?subject_id={subject.id}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    text = response.text
    assert "# Question bank review: Historia" in text
    assert "## Ateny" in text
    assert "Omów demokrację ateńską." in text and "Kim byli heloci?" in text
    assert "Review: **OK**" in text
    assert "Review: **Weak** — Brakuje punktu." in text
    assert passages[0].locator in text
    assert client.get("/api/questions/reviews/export?subject_id=999").status_code == 404


def test_export_labels_bad(client, world):
    subject, _, _, first, _ = world
    put(client, first.id, "bad")
    text = client.get(f"/api/questions/reviews/export?subject_id={subject.id}").text
    assert "Review: **Bad**" in text


def test_migration_downgrade_and_upgrade_again(tmp_path):
    from alembic import command
    from alembic.config import Config
    from test_topics_support import BACKEND

    url = f"sqlite:///{tmp_path.as_posix()}/migrated.db"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()
    command.upgrade(cfg, "0005_rounds")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0005_rounds")
    assert "question_review" not in inspect(create_engine(url)).get_table_names()
    command.upgrade(cfg, "head")
    assert "question_review" in inspect(create_engine(url)).get_table_names()
