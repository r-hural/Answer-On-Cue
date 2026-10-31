import hashlib
import zipfile
from pathlib import Path

import pytest

from distribution.package import PackageError, build_package

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def dist(tmp_path) -> Path:
    path = tmp_path / "dist"
    (path / "assets").mkdir(parents=True)
    (path / "index.html").write_text("<html></html>", encoding="utf-8")
    (path / "assets" / "a.js").write_text("1", encoding="utf-8")
    return path


def names(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as archive:
        return archive.namelist()


def test_zip_contains_runtime_only(dist, tmp_path):
    zip_path = build_package(REPO_ROOT, dist, tmp_path / "out")
    entries = names(zip_path)
    for wanted in (
        "backend/app/main.py",
        "backend/migrations/versions/0005_rounds.py",
        "backend/alembic.ini",
        "backend/distribution/launcher.py",
        "frontend/dist/index.html",
        "frontend/dist/assets/a.js",
        "VERSION",
        "release.json",
        "uninstall.ps1",
    ):
        assert wanted in entries, wanted
    for entry in entries:
        for banned in ("tests/", "__pycache__", ".env", "dev.txt", "docs/", "node_modules", ".db"):
            assert banned not in entry, entry
    assert entries == sorted(entries)


def test_reproducible_sha(dist, tmp_path):
    first = build_package(REPO_ROOT, dist, tmp_path / "a")
    second = build_package(REPO_ROOT, dist, tmp_path / "b")
    digest = hashlib.sha256(first.read_bytes()).hexdigest()
    assert digest == hashlib.sha256(second.read_bytes()).hexdigest()
    line = (first.parent / f"{first.name}.sha256").read_text(encoding="ascii")
    assert line == f"{digest}  {first.name}\n"


def test_install_script_has_repo(dist, tmp_path):
    build_package(REPO_ROOT, dist, tmp_path / "out")
    text = (tmp_path / "out" / "install.ps1").read_text(encoding="ascii")
    assert "__RELEASES_REPO__" not in text
    assert "r-hural/Answer-On-Cue" in text


def test_missing_dist_error(tmp_path):
    with pytest.raises(
        PackageError, match="frontend/dist/index.html is missing. Build the interface."
    ):
        build_package(REPO_ROOT, tmp_path / "none", tmp_path / "out")


def test_version_override(dist, tmp_path):
    zip_path = build_package(REPO_ROOT, dist, tmp_path / "out", "0.1.1")
    assert zip_path.name == "answeroncue-0.1.1.zip"
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.read("VERSION").decode().strip() == "0.1.1"
    with pytest.raises(PackageError):
        build_package(REPO_ROOT, dist, tmp_path / "out", "junk")


def test_sandbox_file_maps_out_dir_read_only(dist, tmp_path):
    build_package(REPO_ROOT, dist, tmp_path / "out")
    text = (tmp_path / "out" / "answeroncue-sandbox.wsb").read_text(encoding="utf-8")
    assert str((tmp_path / "out").resolve()) in text
    assert "<ReadOnly>true</ReadOnly>" in text
    assert "C:\\Users\\WDAGUtilityAccount\\Desktop\\release" in text
