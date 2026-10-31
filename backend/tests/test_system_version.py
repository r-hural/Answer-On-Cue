import re

from app.config import REPO_ROOT
from app.features.system.version import app_version, releases_repo


def test_version_file_is_semver():
    text = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", text)


def test_app_version_reads_file():
    assert app_version() == "0.1.0"


def test_releases_repo_from_release_json(monkeypatch):
    monkeypatch.delenv("AOC_RELEASES_REPO", raising=False)
    assert releases_repo() == "r-hural/Answer-On-Cue"


def test_releases_repo_env_override(monkeypatch):
    monkeypatch.setenv("AOC_RELEASES_REPO", "someone/else")
    assert releases_repo() == "someone/else"
