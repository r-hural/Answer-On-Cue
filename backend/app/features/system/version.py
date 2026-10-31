import json
import os

from app.config import REPO_ROOT


def app_version() -> str:
    try:
        text = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"
    return text or "0.0.0"


def releases_repo() -> str:
    override = os.environ.get("AOC_RELEASES_REPO")
    if override:
        return override
    try:
        data = json.loads((REPO_ROOT / "release.json").read_text(encoding="utf-8"))
        return str(data["repo"])
    except (OSError, ValueError, KeyError):
        return ""
