"""Builds the release folder: zip, checksum, install.ps1 and a Windows Sandbox file."""

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

from distribution.versions import parse

FIXED_TIME = (1980, 1, 1, 0, 0, 0)
EXCLUDED_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "node_modules"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite3"}
SANDBOX_DESKTOP = "C:\\Users\\WDAGUtilityAccount\\Desktop\\release"


class PackageError(Exception):
    pass


def read_version(repo_root: Path) -> str:
    try:
        text = (repo_root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PackageError("The VERSION file is missing.") from exc
    if parse(text) is None or text.startswith("v"):
        raise PackageError(f"Invalid version in the VERSION file: {text!r}.")
    return text


def _walk(base: Path, prefix: str) -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    if not base.is_dir():
        return found
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(base)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.suffix in EXCLUDED_SUFFIXES or path.name == ".env":
            continue
        found.append((path, f"{prefix}/{relative.as_posix()}"))
    return found


def package_files(repo_root: Path, dist_dir: Path) -> list[tuple[Path, str]]:
    backend = repo_root / "backend"
    files: list[tuple[Path, str]] = [
        (repo_root / "VERSION", "VERSION"),
        (repo_root / "release.json", "release.json"),
        (repo_root / "scripts" / "uninstall.ps1", "uninstall.ps1"),
        (backend / "alembic.ini", "backend/alembic.ini"),
    ]
    files += _walk(backend / "app", "backend/app")
    files += _walk(backend / "migrations", "backend/migrations")
    files += _walk(backend / "distribution", "backend/distribution")
    for path in sorted((backend / "requirements").glob("*.txt")):
        if path.name != "dev.txt":
            files.append((path, f"backend/requirements/{path.name}"))
    files += _walk(dist_dir, "frontend/dist")
    return sorted(files, key=lambda item: item[1])


def _releases_repo(repo_root: Path) -> str:
    try:
        return str(json.loads((repo_root / "release.json").read_text(encoding="utf-8"))["repo"])
    except (OSError, ValueError, KeyError) as exc:
        raise PackageError("The release.json file is missing or invalid.") from exc


def _sandbox_xml(out_dir: Path) -> str:
    return (
        "<Configuration>\n"
        "  <MappedFolders>\n"
        "    <MappedFolder>\n"
        f"      <HostFolder>{out_dir.resolve()}</HostFolder>\n"
        f"      <SandboxFolder>{SANDBOX_DESKTOP}</SandboxFolder>\n"
        "      <ReadOnly>true</ReadOnly>\n"
        "    </MappedFolder>\n"
        "  </MappedFolders>\n"
        "  <Networking>Enable</Networking>\n"
        "</Configuration>\n"
    )


def build_package(
    repo_root: Path, dist_dir: Path, out_dir: Path, version: str | None = None
) -> Path:
    if not (dist_dir / "index.html").is_file():
        raise PackageError("frontend/dist/index.html is missing. Build the interface.")
    version = version or read_version(repo_root)
    if parse(version) is None:
        raise PackageError(f"Invalid version: {version!r}.")
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_name = f"answeroncue-{version}.zip"
    zip_path = out_dir / zip_name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source, arcname in package_files(repo_root, dist_dir):
            info = zipfile.ZipInfo(arcname, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            data = source.read_bytes()
            if arcname == "VERSION":
                data = f"{version}\n".encode()
            archive.writestr(info, data)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (out_dir / f"{zip_name}.sha256").write_text(f"{digest}  {zip_name}\n", encoding="ascii")
    script = (repo_root / "scripts" / "install.ps1").read_text(encoding="utf-8")
    script = script.replace("__RELEASES_REPO__", _releases_repo(repo_root))
    (out_dir / "install.ps1").write_bytes(script.encode("ascii"))
    (out_dir / "answeroncue-sandbox.wsb").write_text(_sandbox_xml(out_dir), encoding="utf-8")
    return zip_path


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(prog="distribution.package")
    parser.add_argument("--dist", type=Path, default=repo_root / "frontend" / "dist")
    parser.add_argument("--out", type=Path, default=repo_root / "release-out")
    parser.add_argument("--version")
    args = parser.parse_args(argv)
    try:
        zip_path = build_package(repo_root, args.dist, args.out, args.version)
    except PackageError as exc:
        print(f"PACKAGE FAILED: {exc}")
        return 1
    print(f"PACKAGE OK {zip_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
