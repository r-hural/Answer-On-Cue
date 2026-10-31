import json

import pytest
from pydantic import ValidationError
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.rounds.models import Round
from app.features.rounds.schemas import FollowUpConfig, RoundConfig


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
    return client.post("/api/rounds", json={"subject_id": world["subject"].id, "config": config})


def test_old_config_json_loads_with_follow_ups_off(client, world, db):
    created = create(client, world, question_count=2)
    assert created.status_code == 201, created.text
    row = db.get(Round, created.json()["id"])
    stored = json.loads(row.config_json)
    del stored["follow_ups"]  # a round saved before follow-ups existed
    row.config_json = json.dumps(stored)
    db.commit()
    state = client.get(f"/api/rounds/{row.id}").json()
    assert state["config"]["follow_ups"] == {
        "enabled": False,
        "prep_seconds": 15,
        "answer_seconds": 60,
    }
    assert (state["part"], state["follow_up"]) == ("main", None)
    assert RoundConfig.model_validate_json(row.config_json).follow_ups == FollowUpConfig()


def test_timer_bounds(client, world):
    for follow_ups in ({"prep_seconds": -1}, {"answer_seconds": 4}, {"prep_seconds": 601}):
        response = create(client, world, follow_ups={"enabled": True, **follow_ups})
        assert response.status_code == 422, follow_ups
    response = create(
        client,
        world,
        question_count=2,
        follow_ups={"enabled": True, "prep_seconds": 0, "answer_seconds": 5},
    )
    assert response.status_code == 201, response.text
    assert response.json()["config"]["follow_ups"] == {
        "enabled": True,
        "prep_seconds": 0,
        "answer_seconds": 5,
    }


def test_extra_key_forbidden():
    with pytest.raises(ValidationError):
        FollowUpConfig(enabled=True, count=2)
    with pytest.raises(ValidationError):
        RoundConfig(scope={"kind": "subject"}, follow_ups={"enabled": True, "max": 1})
