import logging
from pathlib import Path

log = logging.getLogger(__name__)

DB_FILE = "answeroncue.db"
LEGACY_DB_FILE = "proba-glosu.db"


def migrate_legacy_db(data_dir: Path) -> Path | None:
    """Rename the database file of the old product name to the current one, once.

    Returns the renamed file, or None when nothing was renamed. When both files exist the new one
    wins and the old one is left untouched.
    """
    old = data_dir / LEGACY_DB_FILE
    new = data_dir / DB_FILE
    if not old.is_file():
        return None
    if new.exists():
        log.warning("Both %s and %s exist; using %s and leaving %s untouched.", old, new, new, old)
        return None
    old.rename(new)
    return new
