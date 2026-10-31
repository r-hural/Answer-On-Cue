from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings


def make_engine(url: str) -> Engine:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return create_engine(url)
    if parsed.database and parsed.database != ":memory:":
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


_session_factory: sessionmaker[Session] | None = None
_session_url: str | None = None


def SessionLocal(url: str | None = None) -> Session:  # noqa: N802 - conventional factory name
    global _session_factory, _session_url
    url = url or get_settings().effective_database_url
    if _session_factory is None or _session_url != url:
        _session_factory = sessionmaker(bind=make_engine(url), expire_on_commit=False)
        _session_url = url
    return _session_factory()


def get_session(settings: Annotated[Settings, Depends(get_settings)]) -> Iterator[Session]:
    """FastAPI dependency. Uses the app's settings (create_app overrides get_settings)."""
    with SessionLocal(settings.effective_database_url) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
