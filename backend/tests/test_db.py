from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.registry import import_all_models
from app.db.session import make_engine
from app.features.library.models import Subject

BACKEND = Path(__file__).resolve().parent.parent


def upgrade(url: str) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()
    command.upgrade(cfg, "head")


@pytest.fixture
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path.as_posix()}/s.db")
    import_all_models()
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_upgrade_creates_subject_table(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/t.db"
    upgrade(url)
    inspector = inspect(create_engine(url))
    assert "subject" in inspector.get_table_names()
    columns = {c["name"] for c in inspector.get_columns("subject")}
    assert columns == {"id", "name", "description", "created_at", "updated_at", "language"}


def test_subject_name_unique(session):
    session.add(Subject(name="Historia"))
    session.commit()
    session.add(Subject(name="Historia"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_metadata_matches_migrations(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/m.db"
    upgrade(url)
    import_all_models()
    with create_engine(url).connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_polish_text_roundtrip(session):
    name = "Źródła prawa – ćwiczenia"
    session.add(Subject(name=name))
    session.commit()
    session.expire_all()
    assert session.query(Subject).one().name == name


def test_get_session_uses_app_settings(settings):
    from fastapi.testclient import TestClient

    from app.db.session import SessionDep
    from app.main import create_app

    app = create_app(settings)

    @app.get("/api/_test/db-url")
    def db_url(session: SessionDep) -> dict[str, str]:
        return {"url": str(session.get_bind().url.database)}

    # the SPA/api catch-all routes are registered first; move the test route to the front
    app.router.routes.insert(0, app.router.routes.pop())
    url = TestClient(app).get("/api/_test/db-url").json()["url"]
    assert settings.data_dir.as_posix() in url
