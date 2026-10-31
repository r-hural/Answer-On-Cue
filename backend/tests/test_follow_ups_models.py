import json
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_rounds_models import _round
from test_topics_support import BACKEND, upgrade

from app.db.base import Base
from app.db.registry import import_all_models
from app.db.session import make_engine
from app.features.follow_ups.models import FollowUp


@pytest.fixture
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path.as_posix()}/s.db")
    import_all_models()
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()
    return cfg


def _follow_up(row, item, attempt) -> FollowUp:
    return FollowUp(
        round_id=row.id,
        main_item_id=item.id,
        main_attempt_id=attempt.id,
        status="generating",
        started_at=datetime(2026, 10, 23, 9, 0, tzinfo=UTC),
    )


def _columns(url: str, table: str) -> set[str]:
    return {c["name"] for c in inspect(create_engine(url)).get_columns(table)}


def test_upgrade_creates_table_and_column(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/m.db"
    upgrade(url)
    assert "follow_up" in inspect(create_engine(url)).get_table_names()
    assert "part" in _columns(url, "round")
    import_all_models()
    with create_engine(url).connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def _insert_round(url: str) -> None:
    with make_engine(url).begin() as conn:
        conn.execute(text("INSERT INTO subject (name) VALUES ('Historia')"))
        conn.execute(
            text(
                "INSERT INTO round (subject_id, round_type, status, phase, current_index, "
                "config_json, seed) VALUES (1, 'standard', 'active', 'question', 0, :c, 7)"
            ),
            {"c": json.dumps({})},
        )


def test_existing_rounds_get_part_main(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/p.db"
    cfg = _config(url)
    command.upgrade(cfg, "0008_transcript_report")
    _insert_round(url)
    command.upgrade(cfg, "head")
    with create_engine(url).connect() as conn:
        assert conn.execute(text("SELECT part FROM round")).scalar_one() == "main"


def test_one_follow_up_per_main_item(session):
    _, row, item, attempt, _, _ = _round(session)
    session.add(_follow_up(row, item, attempt))
    session.commit()
    session.add(_follow_up(row, item, attempt))
    with pytest.raises(IntegrityError):
        session.commit()


def test_subject_delete_cascades(session):
    subject, row, item, attempt, _, _ = _round(session)
    session.add(_follow_up(row, item, attempt))
    session.commit()
    session.delete(subject)
    session.commit()
    assert session.query(FollowUp).count() == 0


def test_downgrade_to_0008_and_back(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/d.db"
    cfg = _config(url)
    command.upgrade(cfg, "head")
    _insert_round(url)
    command.downgrade(cfg, "0008_transcript_report")
    assert "follow_up" not in inspect(create_engine(url)).get_table_names()
    assert "part" not in _columns(url, "round")
    with create_engine(url).connect() as conn:  # the round survived the column drop
        assert conn.execute(text("SELECT count(*) FROM round")).scalar_one() == 1
    command.upgrade(cfg, "head")
    assert "follow_up" in inspect(create_engine(url)).get_table_names()
    assert "part" in _columns(url, "round")
