"""Strict and expanded rounds draw from separate pools."""

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_rounds_support import make_world
from test_topics_support import make_question, make_subject, upgrade

from app.db.session import SessionLocal
from app.features.rounds.models import RoundItem
from app.features.rounds.schemas import RoundConfig, Scope
from app.features.rounds.selection import Candidate, eligible

SUBJECT = Scope(kind="subject")


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    """3 materials and 3 expanded active questions (make_world has 8; two are retired)."""
    world = make_world(db)
    questions = world["questions"]
    for q in questions[3:6]:
        q.source_policy = "expanded"
    for q in questions[6:]:
        q.status = "retired"
    db.commit()
    world["materials"] = {q.id for q in questions[:3]}
    world["expanded"] = {q.id for q in questions[3:6]}
    return world


def start(client, subject_id, policy, count):
    return client.post(
        "/api/rounds",
        json={
            "subject_id": subject_id,
            "config": {
                "scope": {"kind": "subject"},
                "question_count": count,
                "source_policy": policy,
            },
        },
    )


def item_ids(db, round_id) -> set[int]:
    db.expire_all()
    return set(
        db.scalars(select(RoundItem.question_version_id).where(RoundItem.round_id == round_id))
    )


def test_config_accepts_expanded_and_defaults_materials():
    assert RoundConfig(scope=SUBJECT).source_policy == "materials"
    config = RoundConfig(scope=SUBJECT, source_policy="expanded")
    assert RoundConfig.model_validate_json(config.model_dump_json()).source_policy == "expanded"
    with pytest.raises(ValidationError):
        RoundConfig(scope=SUBJECT, source_policy="web")


def test_unknown_policy_is_422(client, world):
    response = start(client, world["subject"].id, "web", 3)
    assert response.status_code == 422


def test_strict_round_never_selects_expanded(client, db, world):
    response = start(client, world["subject"].id, "materials", 3)
    assert response.status_code == 201, response.text
    assert item_ids(db, response.json()["id"]) == world["materials"]
    response = start(client, world["subject"].id, "materials", 4)
    assert response.status_code == 409
    assert response.json()["detail"]["available"] == 3


def test_expanded_round_selects_only_expanded(client, db, world):
    response = start(client, world["subject"].id, "expanded", 3)
    assert response.status_code == 201, response.text
    assert item_ids(db, response.json()["id"]) == world["expanded"]
    assert response.json()["config"]["source_policy"] == "expanded"

    other = make_subject(db, "Biologia")
    for n in range(3):
        make_question(db, other.id, None, f"Pytanie biologiczne numer {n}.")
    response = start(client, other.id, "expanded", 1)
    assert response.status_code == 409
    assert response.json()["detail"]["available"] == 0


def cand(qid: int, **over) -> Candidate:
    base = dict(
        question_version_id=qid,
        family_id=qid,
        primary_topic_id=1,
        style="open",
        difficulty="recall",
        document_ids=[],
        last_asked_round=None,
    )
    base.update(over)
    return Candidate(**base)


def test_eligible_filters_policy():
    pool = [cand(1), cand(2, source_policy="expanded"), cand(3, source_policy="materials")]
    assert pool[0].source_policy == "materials"
    strict = RoundConfig(scope=SUBJECT)
    expanded = RoundConfig(scope=SUBJECT, source_policy="expanded")
    assert [c.question_version_id for c in eligible(pool, strict)] == [1, 3]
    assert [c.question_version_id for c in eligible(pool, expanded)] == [2]
