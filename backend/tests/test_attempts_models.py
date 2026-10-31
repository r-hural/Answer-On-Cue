import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_rounds_models import _round
from test_topics_support import upgrade

from app.db.base import Base
from app.db.registry import import_all_models
from app.db.session import make_engine
from app.features.attempts.models import AttemptProcessing


@pytest.fixture
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path.as_posix()}/s.db")
    import_all_models()
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _processing(attempt_id: int) -> AttemptProcessing:
    return AttemptProcessing(attempt_id=attempt_id, status="processing", stage="saved")


def test_upgrade_creates_table(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/t.db"
    upgrade(url)
    assert "attempt_processing" in set(inspect(create_engine(url)).get_table_names())


def test_metadata_matches_migrations(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/m.db"
    upgrade(url)
    import_all_models()
    with create_engine(url).connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_defaults(session):
    *_, attempt, _, _ = _round(session)
    row = _processing(attempt.id)
    session.add(row)
    session.commit()
    session.refresh(row)
    assert (row.transcribe_tries, row.assess_tries) == (0, 0)
    assert row.transcript_text is None and row.failure_kind is None


def test_subject_delete_cascades(session):
    subject, *_, attempt, _, _ = _round(session)
    session.add(_processing(attempt.id))
    session.commit()
    session.delete(subject)
    session.commit()
    assert session.query(AttemptProcessing).count() == 0


def test_attempt_id_unique(session):
    *_, attempt, _, _ = _round(session)
    session.add(_processing(attempt.id))
    session.commit()
    session.add(_processing(attempt.id))
    with pytest.raises(IntegrityError):
        session.commit()


def test_downgrade_and_upgrade_again(tmp_path):
    # Also covers test_questions_review_api's downgrade check, whose "-1" assumed 0006 was head.
    from alembic import command
    from alembic.config import Config
    from test_topics_support import BACKEND

    url = f"sqlite:///{tmp_path.as_posix()}/d.db"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0006_question_review")
    tables = inspect(create_engine(url)).get_table_names()
    assert "attempt_processing" not in tables and "question_review" in tables
    command.downgrade(cfg, "0005_rounds")
    assert "question_review" not in inspect(create_engine(url)).get_table_names()
    command.upgrade(cfg, "head")
    tables = inspect(create_engine(url)).get_table_names()
    assert {"attempt_processing", "question_review"} <= set(tables)
