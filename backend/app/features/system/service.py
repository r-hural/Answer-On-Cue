"""Update check and installer hand-off for the installed (per-user) app."""

import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2

from distribution.versions import newest, parse

CACHE_TTL = timedelta(hours=6)
LOCK_MAX_AGE = timedelta(minutes=15)
CHECK_TIMEOUT = 10.0

NOT_INSTALLED_CHECK = "Updates only work in the installed version."
NO_RELEASE = "No version has been published yet."
NETWORK_ERROR = "Could not check for updates (no connection)."
CHECK_ERROR = "Could not check for updates."
NOT_INSTALLED_UPDATE = "Updating only works in the installed version."
UP_TO_DATE = "You already have the latest version."
UPDATE_RUNNING = "An update is already in progress."
FETCH_ERROR = "Could not download the update installer."


@dataclass
class CheckResult:
    latest: str | None
    checked_at: datetime | None
    message: str | None


# (data_dir, key) -> result; the file data/update-check.json survives restarts.
_memory: dict[tuple[str, str], CheckResult] = {}


def reset_cache() -> None:
    _memory.clear()


def install_root() -> Path | None:
    value = os.environ.get("AOC_INSTALL_ROOT")
    return Path(value) if value else None


def release_source() -> Path | None:
    value = os.environ.get("AOC_RELEASE_SOURCE")
    return Path(value) if value else None


def update_available(current: str, latest: str | None) -> bool:
    if latest is None:
        return False
    newer, older = parse(latest), parse(current)
    return newer is not None and older is not None and newer > older


def _now() -> datetime:
    return datetime.now(UTC)


def _read_cache(data_dir: Path, key: str) -> CheckResult | None:
    try:
        raw = json.loads((data_dir / "update-check.json").read_text(encoding="utf-8-sig"))
        if raw.get("key") != key:
            return None
        return CheckResult(
            raw.get("latest"), datetime.fromisoformat(raw["checked_at"]), raw.get("message")
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cache(data_dir: Path, key: str, result: CheckResult) -> None:
    if result.checked_at is None:
        return
    payload = {
        "key": key,
        "latest": result.latest,
        "checked_at": result.checked_at.isoformat(),
        "message": result.message,
    }
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "update-check.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def _local_latest(source: Path) -> str | None:
    versions = []
    try:
        for entry in source.glob("answeroncue-*.zip"):
            versions.append(entry.name.removeprefix("answeroncue-").removesuffix(".zip"))
    except OSError:
        return None
    return newest(versions)


def _github_latest(repo: str, client: httpx2.Client) -> tuple[str | None, str | None, bool]:
    """(latest, message, cacheable)."""
    try:
        response = client.get(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
            timeout=CHECK_TIMEOUT,
        )
    except httpx2.HTTPError:
        return None, NETWORK_ERROR, False
    if response.status_code == 404:
        return None, NO_RELEASE, True
    if response.status_code != 200:
        return None, CHECK_ERROR, False
    try:
        tag = str(response.json().get("tag_name", ""))
    except ValueError:
        return None, CHECK_ERROR, False
    latest = newest([tag])
    if latest is None:
        return None, CHECK_ERROR, False
    return latest, None, True


def check_for_update(
    data_dir: Path, repo: str, client: httpx2.Client | None, refresh: bool
) -> CheckResult:
    if install_root() is None:
        return CheckResult(None, None, NOT_INSTALLED_CHECK)
    source = release_source()
    key = f"folder:{source}" if source else f"github:{repo}"
    memory_key = (str(data_dir), key)
    if not refresh:
        cached = _memory.get(memory_key) or _read_cache(data_dir, key)
        if cached and cached.checked_at and _now() - cached.checked_at < CACHE_TTL:
            _memory[memory_key] = cached
            return cached
    if source:
        latest = _local_latest(source)
        message = None if latest else NO_RELEASE
        cacheable = True
    else:
        owned = client is None
        http = client or httpx2.Client(follow_redirects=True)
        try:
            latest, message, cacheable = _github_latest(repo, http)
        finally:
            if owned:
                http.close()
    result = CheckResult(latest, _now(), message)
    if cacheable:
        _memory[memory_key] = result
        _write_cache(data_dir, key, result)
    return result


def read_update_status(data_dir: Path) -> dict | None:
    try:
        raw = json.loads((data_dir / "update-status.json").read_text(encoding="utf-8-sig"))
        return {
            "ok": bool(raw["ok"]),
            "version": str(raw["version"]),
            "from_version": raw.get("from_version"),
            "message": raw.get("message"),
            "at": datetime.fromisoformat(raw["at"]),
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None


def windows_powershell_module_path() -> str:
    """powershell.exe 5.1's default module path. A PowerShell 7 value (app started from a pwsh
    window) breaks 5.1, and 5.1 does not rebuild a missing one, so Get-FileHash is not found."""
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    return os.pathsep.join(
        [
            os.path.join(program_files, "WindowsPowerShell", "Modules"),
            os.path.join(system_root, "system32", "WindowsPowerShell", "v1.0", "Modules"),
        ]
    )


def lock_is_fresh(root: Path) -> bool:
    try:
        modified = (root / "updates" / "update.lock").stat().st_mtime
    except OSError:
        return False
    return _now() - datetime.fromtimestamp(modified, UTC) < LOCK_MAX_AGE


def write_lock(root: Path) -> Path:
    updates = root / "updates"
    updates.mkdir(parents=True, exist_ok=True)
    lock = updates / "update.lock"
    lock.write_text(str(os.getpid()), encoding="utf-8")
    return lock


def fetch_installer(root: Path, repo: str, version: str, client: httpx2.Client | None) -> Path:
    target = root / "updates" / f"install-{version}.ps1"
    source = release_source()
    if source:
        shutil.copyfile(source / "install.ps1", target)
        return target
    url = f"https://github.com/{repo}/releases/download/v{version}/install.ps1"
    owned = client is None
    http = client or httpx2.Client(follow_redirects=True)
    try:
        response = http.get(url, timeout=CHECK_TIMEOUT * 3, follow_redirects=True)
        response.raise_for_status()
        target.write_bytes(response.content)
    finally:
        if owned:
            http.close()
    return target
