"""Names and hosts that may reach the backup and export files."""

import re
from pathlib import Path

from fastapi import HTTPException, Request

from app.local_requests import FORBIDDEN, _host_is_local

BACKUP_NAME = re.compile(r"answeroncue-(?:backup|pre-restore)-\d{4}-\d{2}-\d{2}-\d{6}\.zip")
EXPORT_NAME = re.compile(r"answeroncue-history-\d{4}-\d{2}-\d{2}-\d{6}\.zip")
NOT_FOUND = "File not found."


def require_local_host(request: Request) -> None:
    """Downloads are GETs, so they cannot carry the header; the Host must still be local."""
    if not _host_is_local(request.headers.get("host", "")):
        raise HTTPException(403, FORBIDDEN)


def stored_file(folder: Path, name: str, pattern: re.Pattern[str]) -> Path:
    """The file of a generated name inside the folder, or 404; the name is never a path."""
    if not pattern.fullmatch(name):
        raise HTTPException(404, NOT_FOUND)
    path = folder / name
    if not path.is_file():
        raise HTTPException(404, NOT_FOUND)
    return path
