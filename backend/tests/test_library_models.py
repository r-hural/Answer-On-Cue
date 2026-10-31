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
from app.features.library.models import Document, DocumentVersion, SourcePassage, Subject

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


def _version(document_id: int, version: int) -> DocumentVersion:
    return DocumentVersion(
        document_id=document_id,
        version=version,
        filename="a.txt",
        stored_path="documents/a.txt",
        mime="text/plain",
        size_bytes=1,
        sha256="0" * 64,
        status="imported",
        extracted_text="x",
        char_count=1,
    )


def test_upgrade_creates_library_tables(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/t.db"
    upgrade(url)
    inspector = inspect(create_engine(url))
    assert {"document", "document_version", "source_passage"} <= set(inspector.get_table_names())
    columns = {
        t: {c["name"] for c in inspector.get_columns(t)} for t in inspector.get_table_names()
    }
    assert columns["document"] == {
        "id", "subject_id", "title", "created_at", "updated_at", "chapter_id"
    }  # fmt: skip
    assert columns["document_version"] == {
        "id", "document_id", "version", "filename", "stored_path", "mime", "size_bytes",
        "sha256", "status", "error_message", "extracted_text", "char_count", "page_count",
        "created_at", "updated_at", "carried_over",
    }  # fmt: skip
    assert columns["source_passage"] == {
        "id", "document_version_id", "ordinal", "heading", "locator", "text", "char_start",
        "char_end", "excluded", "previous_id",
    }  # fmt: skip


def test_metadata_matches_migrations(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/m.db"
    upgrade(url)
    import_all_models()
    with create_engine(url).connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_cascade_delete(session):
    subject = Subject(name="Historia")
    session.add(subject)
    session.flush()
    document = Document(subject_id=subject.id, title="Doc")
    session.add(document)
    session.flush()
    version = _version(document.id, 1)
    session.add(version)
    session.flush()
    session.add(
        SourcePassage(
            document_version_id=version.id,
            ordinal=0,
            locator="s. 1",
            text="x",
            char_start=0,
            char_end=1,
        )
    )
    session.commit()
    session.delete(subject)
    session.commit()
    for model in (Document, DocumentVersion, SourcePassage):
        assert session.query(model).count() == 0


def test_version_unique_per_document(session):
    subject = Subject(name="Historia")
    session.add(subject)
    session.flush()
    document = Document(subject_id=subject.id, title="Doc")
    session.add(document)
    session.flush()
    session.add(_version(document.id, 1))
    session.commit()
    session.add(_version(document.id, 1))
    with pytest.raises(IntegrityError):
        session.commit()
