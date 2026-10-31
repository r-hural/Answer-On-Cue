"""Backup and restore of the data folder: the database, the documents and the recordings.

A backup never holds settings or keys: only these three kinds of content go in, by an allow-list,
and files named like a settings file are skipped by name as well. A restore validates the whole
file before it changes anything, keeps a pre-restore backup and rolls back when it fails.
"""

import errno
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
import uuid
import zipfile
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import APP_DIR, Settings
from app.db import session as db_session
from app.features.attempts import pipeline
from app.features.backup import storage
from app.features.backup.schemas import BackupOut, Counts, RestorePreview, RestoreResult
from app.features.follow_ups import service as follow_ups
from app.features.follow_ups.models import FollowUp
from app.features.jobs import service as jobs
from app.features.jobs.models import AiJob
from app.features.languages.profiles import SUPPORTED
from app.features.providers.usage import detach_all
from app.features.rounds.models import Attempt, Round
from app.features.system.version import app_version

logger = logging.getLogger(__name__)

BACKUP_FORMAT = "answeroncue-backup"
FORMAT_VERSION = 1
ALLOWED_TOP = ("manifest.json", "answeroncue.db", "documents/", "recordings/")
DB_MEMBER = "answeroncue.db"
MANIFEST = "manifest.json"
FOLDERS = ("documents", "recordings")
# This computer's spend history: a restore keeps these tables as they are here (rows in the
# backup are replaced by the live rows).
KEEP_ON_RESTORE = ("usage_event",)
SECRET_NAMES = {"settings.env", ".env"}
CHUNK = 1024 * 1024
BACKEND = APP_DIR.parent

NOT_A_BACKUP = "This file is not an AnswerOnCue backup."
UNEXPECTED_FILE = "This backup contains files that do not belong in a backup, so it was not used."
DAMAGED = "This backup is damaged or incomplete."
NEWER_VERSION = "This backup was made by a newer version of AnswerOnCue. Update the app first."
NO_SPACE = "There is not enough free disk space for this."
BUSY = "A round or background work is running. Finish or pause it and try again."
RESTORE_FAILED = "The restore failed; your data is as it was before."
SQLITE_ONLY = "Backup and restore work only with the built-in SQLite database."
RESTORE_GONE = "This restore is no longer available. Choose the backup file again."

TOKEN = re.compile(r"[0-9a-f]{32}")
STALE_STAGING_SECONDS = 24 * 60 * 60
# One restore at a time (two tabs, or a double click): a second one answers BUSY at once.
_restore_lock = threading.Lock()


def _error(status: int, message: str) -> HTTPException:
    return HTTPException(status, message)


def _sqlite_file(settings: Settings) -> Path:
    path = storage.database_file(settings)
    if path is None:
        raise _error(422, SQLITE_ONLY)
    return path


# --- creating a backup -------------------------------------------------------------------


def _snapshot(db_file: Path, target: Path) -> None:
    """A consistent copy of the live database (never a copy of the open file)."""
    source = sqlite3.connect(db_file, timeout=30)
    copy = sqlite3.connect(target)
    try:
        source.backup(copy)
    finally:
        copy.close()
        source.close()


def _snapshot_info(path: Path) -> tuple[Counts, str]:
    """The row counts and the Alembic revision of a database file."""
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        with Session(engine) as session:
            counts = storage.counts(session)
            revision = (
                session.connection()
                .exec_driver_sql("SELECT version_num FROM alembic_version")
                .scalar()
            )
    finally:
        engine.dispose()
    return counts, str(revision)


def _content_files(data_dir: Path):
    """(archive name, path) of every file under documents/ and recordings/, in a fixed order."""
    for folder in FOLDERS:
        root = data_dir / folder
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.name.casefold() in SECRET_NAMES or path.is_symlink() or not path.is_file():
                continue
            yield path.relative_to(data_dir).as_posix(), path


def _add_file(zf: zipfile.ZipFile, name: str, path: Path) -> dict | None:
    try:
        info = zipfile.ZipInfo.from_file(path, name)
        info.compress_type = zipfile.ZIP_STORED if name.endswith(".wav") else zipfile.ZIP_DEFLATED
        digest = hashlib.sha256()
        size = 0
        with (
            path.open("rb") as source,
            zf.open(info, "w", force_zip64=info.file_size >= zipfile.ZIP64_LIMIT) as target,
        ):
            while chunk := source.read(CHUNK):
                digest.update(chunk)
                size += len(chunk)
                target.write(chunk)
    except FileNotFoundError:  # deleted while the backup ran
        return None
    return {"path": name, "bytes": size, "sha256": digest.hexdigest()}


def create_backup(
    session: Session | None,
    settings: Settings,
    *,
    prefix: str = "answeroncue-backup",
    now: datetime,
) -> BackupOut:
    db_file = _sqlite_file(settings)
    folder = settings.data_dir / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    name, now = storage.free_name(folder, prefix, now)
    target = folder / name
    part = folder / f"{name}.part"
    snapshot = folder / f".snapshot-{uuid.uuid4().hex}.db"
    try:
        _snapshot(db_file, snapshot)
        counts, revision = _snapshot_info(snapshot)
        files = []
        with zipfile.ZipFile(part, "w", allowZip64=True) as zf:
            entry = _add_file(zf, DB_MEMBER, snapshot)
            files.append(entry)
            for archive_name, path in _content_files(settings.data_dir):
                entry = _add_file(zf, archive_name, path)
                if entry is not None:
                    files.append(entry)
            manifest = {
                "format": BACKUP_FORMAT,
                "format_version": FORMAT_VERSION,
                "app_version": app_version(),
                "alembic_revision": revision,
                "created_at": now.astimezone(UTC).isoformat(),
                "counts": counts.model_dump(),
                "files": files,
            }
            zf.writestr(MANIFEST, json.dumps(manifest, indent=1), zipfile.ZIP_DEFLATED)
        os.replace(part, target)
    finally:
        snapshot.unlink(missing_ok=True)
        part.unlink(missing_ok=True)
    return BackupOut(
        name=name, bytes=target.stat().st_size, created_at=now.astimezone(UTC), counts=counts
    )


# --- validating a backup ---------------------------------------------------------------------


def _safe_member(name: str) -> bool:
    """A plain relative path of the allowed kinds, never a settings file."""
    if not name or "\\" in name or ":" in name or name.startswith("/"):
        return False
    parts = name.rstrip("/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    if parts[-1].casefold() in SECRET_NAMES:
        return False
    return name in (MANIFEST, DB_MEMBER) or name.startswith(("documents/", "recordings/"))


def _known_revisions() -> set[str]:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return {script.revision for script in ScriptDirectory.from_config(cfg).walk_revisions()}


def _read_manifest(zf: zipfile.ZipFile) -> dict:
    try:
        with zf.open(MANIFEST) as handle:
            raw = handle.read(5 * 1024 * 1024)
        manifest = json.loads(raw)
    except (KeyError, ValueError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise _error(422, NOT_A_BACKUP) from exc
    if not isinstance(manifest, dict) or manifest.get("format") != BACKUP_FORMAT:
        raise _error(422, NOT_A_BACKUP)
    version = manifest.get("format_version")
    if not isinstance(version, int):
        raise _error(422, NOT_A_BACKUP)
    if version > FORMAT_VERSION:
        raise _error(422, NEWER_VERSION)
    revision = manifest.get("alembic_revision")
    if not isinstance(revision, str):
        raise _error(422, NOT_A_BACKUP)
    if revision not in _known_revisions():
        raise _error(422, NEWER_VERSION)
    return manifest


def _inspect(zip_path: Path) -> tuple[dict, dict[str, dict], Counts, datetime]:
    """Everything that can be checked without reading file contents."""
    if not zipfile.is_zipfile(zip_path):
        raise _error(422, NOT_A_BACKUP)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()
            seen: set[str] = set()
            for info in infos:
                if not _safe_member(info.filename):
                    raise _error(422, UNEXPECTED_FILE)
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise _error(422, UNEXPECTED_FILE)
                if info.filename in seen or info.flag_bits & 0x1:
                    raise _error(422, DAMAGED)
                seen.add(info.filename)
            manifest = _read_manifest(zf)
            sizes = {i.filename: i.file_size for i in infos if not i.is_dir()}
    except (zipfile.BadZipFile, OSError) as exc:
        raise _error(422, NOT_A_BACKUP) from exc
    files: dict[str, dict] = {}
    for entry in manifest.get("files") or []:
        if not (
            isinstance(entry, dict)
            and isinstance(entry.get("path"), str)
            and isinstance(entry.get("bytes"), int)
            and isinstance(entry.get("sha256"), str)
        ):
            raise _error(422, DAMAGED)
        if not _safe_member(entry["path"]):
            raise _error(422, UNEXPECTED_FILE)
        files[entry["path"]] = entry
    members = set(sizes) - {MANIFEST}
    if set(files) != members or DB_MEMBER not in files:
        raise _error(422, DAMAGED)
    if any(sizes[path] != entry["bytes"] for path, entry in files.items()):
        raise _error(422, DAMAGED)
    try:
        counts = Counts(**manifest.get("counts", {}))
        created = datetime.fromisoformat(manifest["created_at"])
    except (ValidationError, TypeError, ValueError, KeyError) as exc:
        raise _error(422, DAMAGED) from exc
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return manifest, files, counts, created


def _copy_checked(zf: zipfile.ZipFile, name: str, entry: dict, target: Path | None) -> None:
    """Streams one member (to a file, or nowhere) and compares its sha256 with the manifest."""
    digest = hashlib.sha256()
    handle: BinaryIO | None = None
    try:
        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            handle = target.open("wb")
        with zf.open(name) as source:
            while chunk := source.read(CHUNK):
                digest.update(chunk)
                if handle is not None:
                    handle.write(chunk)
    except (zipfile.BadZipFile, OSError, EOFError, zlib.error) as exc:
        if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
            raise _error(422, NO_SPACE) from exc
        raise _error(422, DAMAGED) from exc
    finally:
        if handle is not None:
            try:
                handle.close()
            except OSError:
                logger.warning("Could not close %s", target)
    if digest.hexdigest() != entry["sha256"]:
        raise _error(422, DAMAGED)


STORED_PATHS = (("recording", "recordings/"), ("document_version", "documents/"))


def _foreign_path(stored_path: str | None, folder: str) -> bool:
    """A stored path that is not a plain relative path inside the folder it belongs to."""
    if not stored_path:
        return False
    return not (stored_path.startswith(folder) and _safe_member(stored_path))


def _check_database(path: Path, revision: str) -> None:
    """Integrity, revision, and that no row points outside data/recordings or data/documents
    (a foreign database must not make the app read or delete other files)."""
    try:
        conn = sqlite3.connect(path)
        try:
            if conn.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise _error(422, DAMAGED)
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            if row is None or row[0] != revision:
                raise _error(422, DAMAGED)
            for table, folder in STORED_PATHS:
                if table not in _tables(conn, "main"):
                    continue
                for (stored,) in conn.execute(f"SELECT stored_path FROM {table}"):
                    if _foreign_path(stored, folder):
                        raise _error(422, UNEXPECTED_FILE)
            # A subject in a language this app does not know could not be practised.
            if "subject" in _tables(conn, "main") and "language" in _columns(
                conn, "main", "subject"
            ):
                for (language,) in conn.execute("SELECT DISTINCT language FROM subject"):
                    if language not in SUPPORTED:
                        raise _error(422, DAMAGED)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise _error(422, DAMAGED) from exc


def _free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def validate_backup(
    settings: Settings, zip_path: Path, session: Session | None = None
) -> RestorePreview:
    """Checks the whole file; the staging folder is the zip's own folder (restore/<token>/)."""
    _sqlite_file(settings)
    manifest, files, counts, created = _inspect(zip_path)
    if _free_bytes(settings.data_dir) < 2 * sum(e["bytes"] for e in files.values()):
        raise _error(422, NO_SPACE)
    check = zip_path.parent / "check.db"
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for name, entry in files.items():
                _copy_checked(zf, name, entry, check if name == DB_MEMBER else None)
        _check_database(check, manifest["alembic_revision"])
    finally:
        check.unlink(missing_ok=True)
    current = Counts()
    newer = 0
    if session is not None:
        current = storage.counts(session)
        started = created.astimezone(UTC)
        newer = session.scalar(select(func.count(Round.id)).where(Round.started_at > started)) or 0
    return RestorePreview(
        token=zip_path.parent.name,
        created_at=created,
        app_version=str(manifest.get("app_version", "")),
        counts=counts,
        current=current,
        newer_rounds=newer,
    )


def _remove_stale_staging(settings: Settings, now: float | None = None) -> None:
    """Removes staging folders left behind for more than a day (a dialog closed mid-upload, a
    crash). Skipped while a restore is applied, so the folder in use is never touched; a
    failure is logged and never fails the upload."""
    if not _restore_lock.acquire(blocking=False):
        return
    try:
        root = settings.data_dir / "restore"
        if not root.is_dir():
            return
        limit = (time.time() if now is None else now) - STALE_STAGING_SECONDS
        for folder in root.iterdir():
            try:
                if not (TOKEN.fullmatch(folder.name) and folder.is_dir()):
                    continue
                newest = max(
                    [folder.stat().st_mtime, *(p.stat().st_mtime for p in folder.iterdir())]
                )
                if newest < limit:
                    shutil.rmtree(folder)
            except OSError:
                logger.warning("Could not remove the old restore folder %s", folder.name)
    except Exception:
        logger.warning("Could not clean up old restore folders", exc_info=True)
    finally:
        _restore_lock.release()


def stage_upload(session: Session, settings: Settings, source: BinaryIO) -> RestorePreview:
    """Streams an uploaded backup to data/restore/<token>/upload.zip and validates it."""
    _sqlite_file(settings)
    _remove_stale_staging(settings)
    token = uuid.uuid4().hex
    staging = settings.data_dir / "restore" / token
    staging.mkdir(parents=True)
    try:
        target = staging / "upload.zip"
        with target.open("wb") as out:
            while chunk := source.read(CHUNK):
                out.write(chunk)
        return validate_backup(settings, target, session)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def discard_staged(settings: Settings, token: str) -> None:
    if TOKEN.fullmatch(token):
        shutil.rmtree(settings.data_dir / "restore" / token, ignore_errors=True)


# --- applying a restore ----------------------------------------------------------------------


def _staging(settings: Settings, token: str) -> Path:
    staging = settings.data_dir / "restore" / token
    if not TOKEN.fullmatch(token) or not (staging / "upload.zip").is_file():
        raise _error(404, RESTORE_GONE)
    return staging


def _ensure_idle(session: Session) -> None:
    """Refuses while an exam round runs or any background work could write meanwhile."""
    if session.scalar(select(Round.id).where(Round.status == "active").limit(1)) is not None:
        raise _error(409, BUSY)
    for attempt_id in session.scalars(
        select(Attempt.id).where(Attempt.processing_status == "processing")
    ):
        if pipeline.is_live(attempt_id):
            raise _error(409, BUSY)
    for follow_up_id in session.scalars(select(FollowUp.id).where(FollowUp.status == "generating")):
        if follow_ups.is_live(follow_up_id):
            raise _error(409, BUSY)
    for job in session.scalars(select(AiJob).where(AiJob.status.in_(("queued", "running")))):
        if jobs.is_live(job):
            raise _error(409, BUSY)


def _upgrade(db_path: Path) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url=sqlite:///{db_path.as_posix()}"]})()
    command.upgrade(cfg, "head")


def _tables(conn: sqlite3.Connection, schema: str) -> set[str]:
    rows = conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table'")
    return {row[0] for row in rows}


def _columns(conn: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f'PRAGMA {schema}.table_info("{table}")')]


def _keep_live_rows(staged: Path, live: Path) -> None:
    """For each KEEP_ON_RESTORE table present on both sides, the live rows replace the staged."""
    conn = sqlite3.connect(staged.resolve().as_uri(), uri=True)
    try:
        conn.execute("ATTACH DATABASE ? AS live", (f"{live.resolve().as_uri()}?mode=ro",))
        _copy_kept(conn)
    finally:
        conn.close()


def _detach_kept(staged: Path) -> None:
    """Kept usage rows lose their links: the restored database may reuse those ids."""
    conn = sqlite3.connect(staged.resolve().as_uri(), uri=True)
    try:
        if "usage_event" in _tables(conn, "main"):
            detach_all(conn)
            conn.commit()
    finally:
        conn.close()


def _copy_kept(conn: sqlite3.Connection) -> None:
    main, live = _tables(conn, "main"), _tables(conn, "live")
    for table in KEEP_ON_RESTORE:
        if table not in main or table not in live:
            continue
        shared = [c for c in _columns(conn, "main", table) if c in _columns(conn, "live", table)]
        cols = ", ".join(f'"{c}"' for c in shared)
        conn.execute(f'DELETE FROM main."{table}"')
        conn.execute(f'INSERT INTO main."{table}" ({cols}) SELECT {cols} FROM live."{table}"')
    conn.commit()


def _had_languages(db_path: Path) -> bool:
    """Whether the backup was made after content languages existed (before the upgrade)."""
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return "content_language" in _tables(conn, "main")
    finally:
        conn.close()


def _languages_after_restore(staged: Path, live: Path, *, had_languages: bool) -> None:
    """A backup from before content languages keeps this computer's language rows (its subjects
    became Polish in the upgrade); a supported language without a row gets a disabled one, and
    every language a restored subject uses is then enabled."""
    conn = sqlite3.connect(staged.resolve().as_uri(), uri=True)
    try:
        conn.execute("ATTACH DATABASE ? AS live", (f"{live.resolve().as_uri()}?mode=ro",))
        if not had_languages and "content_language" in _tables(conn, "live"):
            table = "content_language"
            shared = [
                c for c in _columns(conn, "main", table) if c in _columns(conn, "live", table)
            ]
            cols = ", ".join(f'"{c}"' for c in shared)
            conn.execute(f'DELETE FROM main."{table}"')
            conn.execute(f'INSERT INTO main."{table}" ({cols}) SELECT {cols} FROM live."{table}"')
        # A table without a row for a supported language would hide that language for good.
        conn.executemany(
            "INSERT OR IGNORE INTO main.content_language (code, enabled, voice_provider,"
            " voice_name) VALUES (?, 0, NULL, NULL)",
            [(code,) for code in SUPPORTED],
        )
        conn.execute(
            "UPDATE main.content_language SET enabled = 1"
            " WHERE code IN (SELECT DISTINCT language FROM main.subject)"
        )
        conn.commit()
    finally:
        conn.close()


def _copy_database(source: Path, destination: Path) -> None:
    """Replaces the contents of the live database in one atomic step (no file replace)."""
    src = sqlite3.connect(source)
    dst = sqlite3.connect(destination, timeout=30)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _dispose_engine() -> None:
    """Pooled connections reopen the restored file."""
    factory = db_session._session_factory
    if factory is not None:
        factory.kw["bind"].dispose()


def _roll_back(
    settings: Settings, db_file: Path, staging: Path, aside: list[str], pre_restore: Path
) -> None:
    """Puts the old folders and the old database back (best effort, every step logged)."""
    for name in reversed(aside):
        live = settings.data_dir / name
        old = staging / "old" / name
        try:
            if live.exists():
                shutil.rmtree(live, ignore_errors=True)
            os.replace(old, live)
        except OSError:
            logger.exception("Restore rollback: could not put %s back", name)
    try:
        previous = staging / "rollback.db"
        with zipfile.ZipFile(pre_restore) as zf, zf.open(DB_MEMBER) as src:
            with previous.open("wb") as out:
                shutil.copyfileobj(src, out, CHUNK)
        _copy_database(previous, db_file)
        _dispose_engine()
    except (OSError, sqlite3.Error, zipfile.BadZipFile, KeyError):
        logger.exception("Restore rollback: could not put the database back")


def apply_restore(session: Session, settings: Settings, token: str) -> RestoreResult:
    if not _restore_lock.acquire(blocking=False):
        raise _error(409, BUSY)
    try:
        return _apply_restore(session, settings, token)
    finally:
        _restore_lock.release()


def _apply_restore(session: Session, settings: Settings, token: str) -> RestoreResult:
    db_file = _sqlite_file(settings)
    staging = _staging(settings, token)
    _ensure_idle(session)
    manifest, files, _, _ = _inspect(staging / "upload.zip")
    session.rollback()  # no read snapshot held while the files are replaced
    pre = create_backup(session, settings, prefix="answeroncue-pre-restore", now=storage.utcnow())
    pre_zip = settings.data_dir / "backups" / pre.name
    new = staging / "new"
    staged_db = new / DB_MEMBER
    try:
        # Nothing live has changed yet: on any failure here the pre-restore backup is removed.
        shutil.rmtree(new, ignore_errors=True)
        needed = sum(e["bytes"] for e in files.values()) + files[DB_MEMBER]["bytes"]
        if _free_bytes(settings.data_dir) < needed:  # the pre-restore backup took its share
            raise _error(422, NO_SPACE)
        with zipfile.ZipFile(staging / "upload.zip") as zf:
            for name, entry in files.items():
                _copy_checked(zf, name, entry, new / name)
        try:
            _check_database(staged_db, manifest["alembic_revision"])
            had_languages = _had_languages(staged_db)
            _upgrade(staged_db)
            _keep_live_rows(staged_db, db_file)
            _languages_after_restore(staged_db, db_file, had_languages=had_languages)
            _detach_kept(staged_db)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Restore: the backup could not be prepared")
            raise _error(500, RESTORE_FAILED) from exc
        # Fresh reads: a round may have started while the pre-restore backup was written.
        session.rollback()
        session.expire_all()
        _ensure_idle(session)
        session.rollback()
    except BaseException:
        shutil.rmtree(new, ignore_errors=True)
        pre_zip.unlink(missing_ok=True)
        raise

    aside: list[str] = []
    try:
        _copy_database(staged_db, db_file)
        _dispose_engine()
        (staging / "old").mkdir(exist_ok=True)
        for name in FOLDERS:
            live = settings.data_dir / name
            if live.exists():
                os.replace(live, staging / "old" / name)
                aside.append(name)
            if (new / name).exists():
                os.replace(new / name, live)
            else:
                live.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.exception("Restore failed; rolling back")
        _roll_back(settings, db_file, staging, aside, pre_zip)
        shutil.rmtree(staging, ignore_errors=True)
        raise _error(500, RESTORE_FAILED) from exc

    shutil.rmtree(staging, ignore_errors=True)
    session.close()
    return RestoreResult(restored=storage.counts(session), pre_restore_backup=pre.name)
