"""Automatic clean-up of old pre-restore backups and exports.

Only the files the app makes on its own are pruned: pre-restore backups (the newest N stay) and
history exports (deleted after N days). Backups you make yourself, half-written `.part` files
and anything else in the folders are never touched. Nothing is deleted while a restore runs,
so the pre-restore backup it is writing and may roll back to always survives.
"""

import logging
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.features.backup import archive
from app.features.backup.guards import BACKUP_NAME, EXPORT_NAME

logger = logging.getLogger(__name__)

PRE_RESTORE_PREFIX = "answeroncue-pre-restore-"
EXPORT_PREFIX = "answeroncue-history-"
STAMP = "%Y-%m-%d-%H%M%S"
PRUNE_INTERVAL = timedelta(days=1)

_lock = threading.Lock()
_last_prune: dict[str, datetime] = {}


@dataclass(frozen=True)
class Pruned:
    backups: list[str]
    exports: list[str]


def _files(folder: Path, prefix: str, pattern: re.Pattern[str]) -> list[tuple[str, datetime]]:
    """The generated files of one kind, oldest first, with the local time in their names."""
    if not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        name = path.name
        if not (name.startswith(prefix) and pattern.fullmatch(name)):
            continue
        try:
            if path.is_symlink() or not path.is_file():
                continue
            made = datetime.strptime(name[len(prefix) : -len(".zip")], STAMP).astimezone()
        except (OSError, ValueError):
            continue
        found.append((name, made))
    return sorted(found)


def _delete(folder: Path, names: list[str], deleted: list[str]) -> None:
    for name in names:
        try:
            (folder / name).unlink()
        except OSError:
            logger.warning("Automatic clean-up could not delete %s", name, exc_info=True)
            continue
        deleted.append(name)


def prune(
    settings: Settings, *, now: datetime, keep: int | None = None, days: int | None = None
) -> Pruned:
    """Deletes the pre-restore backups beyond the newest `keep` and the exports older than
    `days` (0 keeps all). Skipped while a restore holds the lock; never raises."""
    keep = settings.pre_restore_keep if keep is None else keep
    days = settings.export_keep_days if days is None else days
    backups: list[str] = []
    exports: list[str] = []
    if not archive._restore_lock.acquire(blocking=False):
        return Pruned(backups=backups, exports=exports)
    try:
        if keep > 0:
            folder = settings.data_dir / "backups"
            found = _files(folder, PRE_RESTORE_PREFIX, BACKUP_NAME)
            _delete(folder, [name for name, _ in found[:-keep]], backups)
        if days > 0:
            folder = settings.data_dir / "exports"
            limit = now - timedelta(days=days)
            found = _files(folder, EXPORT_PREFIX, EXPORT_NAME)
            _delete(folder, [name for name, made in found if made < limit], exports)
    except Exception:
        logger.warning("Automatic clean-up failed", exc_info=True)
    finally:
        archive._restore_lock.release()
    return Pruned(backups=backups, exports=exports)


def prune_if_due(settings: Settings, now: datetime | None = None) -> None:
    """Prunes at most once a day per data folder; never raises."""
    if settings.pre_restore_keep == 0 and settings.export_keep_days == 0:
        return
    now = now or datetime.now(UTC)
    key = str(settings.data_dir)
    with _lock:
        last = _last_prune.get(key)
        if last is not None and now - last < PRUNE_INTERVAL:
            return
        _last_prune[key] = now
    try:
        prune(settings, now=now)
    except Exception:
        logger.warning("Automatic clean-up failed", exc_info=True)
