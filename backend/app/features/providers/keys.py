"""Reads and rewrites backend/.env in place; every line it does not own stays byte for byte."""

import os
import re
import tempfile
import threading
import time
from pathlib import Path

from app.features.providers.types import ProviderName

KEY_ENV_NAMES: dict[ProviderName, str] = {
    "openai": "AOC_OPENAI_API_KEY",
    "anthropic": "AOC_ANTHROPIC_API_KEY",
    "azure_speech": "AOC_AZURE_SPEECH_KEY",
}
AZURE_REGION_ENV_NAME = "AOC_AZURE_SPEECH_REGION"
MASK = "••••"
# Saves from concurrent requests run one at a time (each reads, edits and replaces the whole
# file, so two at once would lose one change). On Windows os.replace also fails while another
# handle has the target open, so the replace is retried for a short, bounded time.
_LOCK = threading.RLock()
REPLACE_ATTEMPTS = 40
REPLACE_PAUSE_S = 0.025
_NEEDS_QUOTES = re.compile(r"[\s#='\"\\]")


def read_env(path: Path) -> list[str]:
    """Lines with their own line endings, so writing them back changes nothing."""
    with _LOCK:
        if not path.exists():
            return []
        return path.read_bytes().decode("utf-8").splitlines(keepends=True)


def _assignment(name: str) -> re.Pattern[str]:
    return re.compile(rf"^[ \t]*(?:export[ \t]+)?{re.escape(name)}[ \t]*=")


def _newline(lines: list[str]) -> str:
    return "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"


def _line_ending(line: str) -> str:
    return line[len(line.rstrip("\r\n")) :]


def _format_value(value: str) -> str:
    if not _NEEDS_QUOTES.search(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write("".join(lines).encode("utf-8"))
        _replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _replace(tmp: str, path: Path) -> None:
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_PAUSE_S)


def set_env_value(path: Path, name: str, value: str) -> None:
    with _LOCK:
        _set_env_value(path, name, value)


def _set_env_value(path: Path, name: str, value: str) -> None:
    lines = read_env(path)
    pattern = _assignment(name)
    newline = _newline(lines)
    assignment = f"{name}={_format_value(value)}"
    result: list[str] = []
    replaced = False
    for line in lines:
        if not pattern.match(line):
            result.append(line)
        elif not replaced:
            result.append(assignment + _line_ending(line))
            replaced = True
        # later duplicates of the same name are dropped so the new value is the one that counts
    if not replaced:
        if result and not result[-1].endswith(("\n", "\r")):
            result[-1] += newline
        result.append(assignment + newline)
    _write(path, result)


def remove_env_value(path: Path, name: str) -> None:
    with _LOCK:
        lines = read_env(path)
        pattern = _assignment(name)
        kept = [line for line in lines if not pattern.match(line)]
        if kept != lines:
            _write(path, kept)


def mask(value: str) -> str:
    return MASK + value[-4:] if len(value) >= 8 else MASK
