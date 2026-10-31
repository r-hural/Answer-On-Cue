import json

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from test_rounds_support import make_world
from test_topics_support import BACKEND, upgrade

from app.db.session import SessionLocal, make_engine
from app.features.rounds.schemas import (
    EXTENSION_FROM_SUMMARY,
    ExtensionConfig,
    RoundConfig,
    Scope,
)


def _config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()
    return cfg


def _columns(url: str, table: str) -> set[str]:
    return {c["name"] for c in inspect(create_engine(url)).get_columns(table)}


def _insert_round(conn, parent: int | None, round_type: str) -> None:
    conn.execute(
        text(
            "INSERT INTO round (subject_id, round_type, status, phase, current_index, "
            "config_json, seed, parent_round_id) "
            "VALUES (1, :t, 'active', 'question', 0, :c, 7, :p)"
        ),
        {"t": round_type, "c": json.dumps({}), "p": parent},
    )


def test_migration_adds_columns(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/m.db"
    cfg = _config(url)
    command.upgrade(cfg, "head")
    assert "parent_round_id" in _columns(url, "round")
    assert "selection" in _columns(url, "round_item")
    engine = make_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO subject (name) VALUES ('Historia')"))
        _insert_round(conn, None, "standard")
        _insert_round(conn, None, "standard")  # two rows without a parent are fine
        _insert_round(conn, 1, "extension")
    with pytest.raises(IntegrityError), engine.begin() as conn:
        _insert_round(conn, 1, "extension")
    command.downgrade(cfg, "0009_follow_ups")
    assert "parent_round_id" not in _columns(url, "round")
    assert "selection" not in _columns(url, "round_item")
    with create_engine(url).connect() as conn:  # the rounds survived the column drop
        assert conn.execute(text("SELECT count(*) FROM round")).scalar_one() == 3
    command.upgrade(cfg, "head")
    assert "parent_round_id" in _columns(url, "round")


def test_config_defaults():
    config = RoundConfig(scope=Scope(kind="subject"))
    assert config.round_type == "standard"
    assert config.extension == ExtensionConfig(enabled=False, question_count=3)
    old = json.loads(config.model_dump_json())
    del old["extension"]
    assert RoundConfig.model_validate_json(json.dumps(old)).extension.enabled is False
    assert RoundConfig(scope=Scope(kind="subject"), round_type="extension").round_type == (
        "extension"
    )


@pytest.fixture
def world(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield make_world(session)


def _create(client, world, **config):
    config.setdefault("scope", {"kind": "subject"})
    return client.post("/api/rounds", json={"subject_id": world["subject"].id, "config": config})


@pytest.mark.parametrize("count", [0, 11])
def test_extension_count_range(client, world, count):
    response = _create(client, world, extension={"enabled": True, "question_count": count})
    assert response.status_code == 422


def test_extension_count_in_range_accepted(client, world):
    response = _create(
        client, world, question_count=2, extension={"enabled": True, "question_count": 10}
    )
    assert response.status_code == 201, response.text
    assert response.json()["config"]["extension"] == {"enabled": True, "question_count": 10}


def test_cannot_create_extension_round_directly(client, world):
    response = _create(client, world, round_type="extension")
    assert response.status_code == 422
    assert EXTENSION_FROM_SUMMARY in json.dumps(response.json()["detail"])
