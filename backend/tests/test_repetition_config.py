import json
from datetime import UTC, datetime

import pytest
from test_coverage_support import make_round
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.rounds.models import Round
from app.features.rounds.schemas import (
    REPEAT_NEEDS_PRACTICE,
    RETRY_FROM_SUMMARY,
    RoundConfig,
    Scope,
)

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    return make_world(db)


def create(client, world, **config):
    config.setdefault("scope", {"kind": "subject"})
    config.setdefault("question_count", 2)
    return client.post("/api/rounds", json={"subject_id": world["subject"].id, "config": config})


def test_config_defaults_off():
    config = RoundConfig(scope=Scope(kind="subject"))
    assert config.immediate_repeat is False
    assert config.retry_round is False
    stored = config.model_dump()
    del stored["immediate_repeat"], stored["retry_round"]
    old = RoundConfig.model_validate_json(json.dumps(stored))  # an older snapshot
    assert (old.immediate_repeat, old.retry_round) == (False, False)
    assert RoundConfig.model_validate({**stored, "round_type": "retry"}).round_type == "retry"


def test_repeat_with_exam_is_422(client, world):
    response = create(client, world, feedback_timing="exam", immediate_repeat=True)
    assert response.status_code == 422
    assert REPEAT_NEEDS_PRACTICE in response.text

    practice = create(client, world, feedback_timing="practice", immediate_repeat=True)
    assert practice.status_code == 201, practice.text
    assert practice.json()["config"]["immediate_repeat"] is True

    exam_retry = create(client, world, feedback_timing="exam", retry_round=True)
    assert exam_retry.status_code == 201, exam_retry.text
    assert exam_retry.json()["config"]["retry_round"] is True


def test_cannot_create_retry_round_directly(client, world):
    response = create(client, world, round_type="retry")
    assert response.status_code == 422
    assert RETRY_FROM_SUMMARY in response.text


def test_old_round_state_has_no_repeat(client, world, db):
    row = make_round(db, world["subject"].id, at=NOW, status="active")
    stored = json.loads(row.config_json)
    del stored["immediate_repeat"], stored["retry_round"]
    db.get(Round, row.id).config_json = json.dumps(stored)
    db.commit()
    response = client.get(f"/api/rounds/{row.id}")
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["can_answer_again"] is False
    assert state["config"]["immediate_repeat"] is False
