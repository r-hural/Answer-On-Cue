import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_rounds_models import _round
from test_topics_support import BACKEND, upgrade

from app.db.base import Base
from app.db.registry import import_all_models
from app.db.session import make_engine
from app.features.reports.models import TranscriptReport


@pytest.fixture
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path.as_posix()}/s.db")
    import_all_models()
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _report(attempt_id: int, text: str = "poprawiony tekst") -> TranscriptReport:
    return TranscriptReport(attempt_id=attempt_id, corrected_text=text)


def test_upgrade_creates_table(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/t.db"
    upgrade(url)
    assert "transcript_report" in set(inspect(create_engine(url)).get_table_names())


def test_metadata_matches_migrations(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/m.db"
    upgrade(url)
    import_all_models()
    with create_engine(url).connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_attempt_id_unique(session):
    *_, attempt, _, _ = _round(session)
    session.add(_report(attempt.id))
    session.commit()
    session.add(_report(attempt.id, "inny"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_subject_delete_cascades(session):
    subject, *_, attempt, _, _ = _round(session)
    session.add(_report(attempt.id))
    session.commit()
    session.delete(subject)
    session.commit()
    assert session.query(TranscriptReport).count() == 0


def test_downgrade_and_upgrade_again(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/d.db"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0007_attempt_processing")
    tables = inspect(create_engine(url)).get_table_names()
    assert "transcript_report" not in tables and "attempt_processing" in tables
    command.upgrade(cfg, "head")
    assert "transcript_report" in inspect(create_engine(url)).get_table_names()
