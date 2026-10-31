"""What the data folder holds: sizes and row counts for the settings page and the manifest."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import Settings
from app.features.backup.schemas import Counts, StorageOut
from app.features.library.models import Document, DocumentVersion, Subject
from app.features.questions.models import QuestionFamily
from app.features.recording.models import Recording
from app.features.rounds.models import Attempt, Round
from app.features.topics.models import Topic


def utcnow() -> datetime:
    return datetime.now(UTC)


def free_name(folder: Path, prefix: str, now: datetime) -> tuple[str, datetime]:
    """A file name stamped with the local time, moved on by seconds until it is unused.

    The name is claimed by creating its `.part` file exclusively, so two requests in the same
    second (two tabs) never write the same file.
    """
    while True:
        name = f"{prefix}-{now.astimezone():%Y-%m-%d-%H%M%S}.zip"
        if not (folder / name).exists():
            try:
                (folder / f"{name}.part").open("xb").close()
            except FileExistsError:
                pass
            else:
                return name, now
        now += timedelta(seconds=1)


def folder_bytes(path: Path) -> int:
    """Total size of the files under a folder (0 when it does not exist)."""
    total = 0
    stack = [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def database_file(settings: Settings) -> Path | None:
    """The SQLite file behind the settings, or None for any other database."""
    parsed = make_url(settings.effective_database_url)
    if parsed.get_backend_name() != "sqlite" or not parsed.database:
        return None
    if parsed.database == ":memory:":
        return None
    return Path(parsed.database)


def counts(session: Session) -> Counts:
    """One statement whatever the data size."""
    row = session.execute(
        select(
            select(func.count()).select_from(Subject).scalar_subquery(),
            select(func.count()).select_from(Document).scalar_subquery(),
            select(func.count()).select_from(DocumentVersion).scalar_subquery(),
            select(func.count()).select_from(Topic).scalar_subquery(),
            select(func.count()).select_from(QuestionFamily).scalar_subquery(),
            select(func.count()).select_from(Round).scalar_subquery(),
            select(func.count())
            .select_from(Attempt)
            .where(Attempt.outcome != "pending")
            .scalar_subquery(),
            select(func.count())
            .select_from(Recording)
            .where(Recording.audio_deleted_at.is_(None))
            .scalar_subquery(),
        )
    ).one()
    return Counts(
        subjects=row[0],
        documents=row[1],
        document_versions=row[2],
        topics=row[3],
        questions=row[4],
        rounds=row[5],
        answers=row[6],
        recordings=row[7],
    )


def storage(session: Session, settings: Settings) -> StorageOut:
    data_dir = settings.data_dir
    db_file = database_file(settings)
    present, deleted = session.execute(
        select(
            func.count().filter(Recording.audio_deleted_at.is_(None)),
            func.count().filter(Recording.audio_deleted_at.is_not(None)),
        )
    ).one()
    return StorageOut(
        data_dir=str(data_dir),
        total_bytes=folder_bytes(data_dir),
        database_bytes=db_file.stat().st_size if db_file and db_file.is_file() else 0,
        documents_bytes=folder_bytes(data_dir / "documents"),
        recordings_bytes=folder_bytes(data_dir / "recordings"),
        recordings=present,
        recordings_deleted=deleted,
        audio_retention=settings.audio_retention,
        pre_restore_keep=settings.pre_restore_keep,
        export_keep_days=settings.export_keep_days,
    )
