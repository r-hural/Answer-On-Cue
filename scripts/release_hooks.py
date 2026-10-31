"""The project's hooks for the release toolkit: what only AnswerOnCue can check.

Each command prints one JSON object on stdout ({"problems": [...], "warnings": [...], "data":
{...}}) and exits 0 (ok), 1 (problems) or 2 (error). Standard library only.

  python scripts/release_hooks.py audio
      In the exported tree (cwd): every audio or video file must be declared synthetic by a
      fixtures.json next to it or elsewhere (`sapi_voice` and `files`, matched by sha256), so a
      recording of a real voice never ships.
  python scripts/release_hooks.py build --out DIR
      In the public tree (cwd): `npm ci` when frontend/node_modules is missing, then
      scripts/package.ps1 -Out DIR with this interpreter.
  python scripts/release_hooks.py assets --out DIR --version X.Y.Z
      The built release folder: VERSION inside the zip is X.Y.Z and install.ps1 names the repo
      in release.json.
  python scripts/release_hooks.py smoke --version X.Y.Z --folder DIR
      Installs the published release as a stranger would: downloads install.ps1 from the
      GitHub release and runs it with AOC_INSTALL_ROOT inside DIR (shortcuts there too, no
      uninstall registry entry), a free port seeded in data/launcher.json; then checks
      /api/health and /, and stops the app with POST /api/system/shutdown. Never touches an
      existing install. The rows are in data.rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

AUDIO_SUFFIXES = (
    ".wav",
    ".opus",
    ".aac",
    ".wma",
    ".mp3",
    ".m4a",
    ".ogg",
    ".webm",
    ".mp4",
    ".aif",
    ".aiff",
    ".flac",
)
FIXTURE_MANIFEST = "fixtures.json"
SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
}
ZIP_NAME = "answeroncue-{v}.zip"
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
HEADER = {"X-AnswerOnCue": "1"}


def emit(
    problems: list,
    warnings: list | None = None,
    data: dict | None = None,
    error: bool = False,
) -> int:
    print(
        json.dumps(
            {"problems": problems, "warnings": warnings or [], "data": data or {}}
        )
    )
    return 2 if error else (1 if problems else 0)


def public_repo(tree: Path) -> str:
    return json.loads((Path(tree) / "release.json").read_text(encoding="utf-8"))["repo"]


# --- audio provenance (extra_checks) -----------------------------------------------------------


def tree_files(tree: Path) -> list[tuple[str, Path]]:
    found = []
    for root, dirs, files in os.walk(tree):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in files:
            path = Path(root) / name
            found.append((path.relative_to(tree).as_posix(), path))
    return sorted(found)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def synthetic_audio(tree: Path) -> dict[str, str]:
    """sha256 -> provenance of every audio file a fixtures.json declares as synthetic (SAPI)."""
    known: dict[str, str] = {}
    for rel, path in tree_files(tree):
        if path.name != FIXTURE_MANIFEST:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        voice = data.get("sapi_voice") if isinstance(data, dict) else None
        files = data.get("files") if isinstance(data, dict) else None
        if not isinstance(voice, str) or not voice or not isinstance(files, dict):
            continue
        for name in files:
            audio = path.parent / str(name)
            if audio.is_file():
                known[_sha(audio)] = f"synthetic (Windows SAPI voice {voice}, {rel})"
    return known


def audio(tree: Path) -> int:
    tree = Path(tree)
    known = synthetic_audio(tree)
    rows, problems = [], []
    for rel, path in tree_files(tree):
        if path.suffix.casefold() not in AUDIO_SUFFIXES:
            continue
        origin = known.get(_sha(path))
        rows.append({"path": rel, "provenance": origin})
        if origin is None:
            problems.append(
                {
                    "kind": "audio-provenance",
                    "path": rel,
                    "detail": f"{rel}: audio not declared synthetic in a {FIXTURE_MANIFEST} "
                    "(a real voice never ships)",
                }
            )
    return emit(problems, data={"audio": rows})


# --- build and check the assets ----------------------------------------------------------------


def build(tree: Path, out: Path, runner=subprocess.run) -> int:
    tree = Path(tree)
    frontend = tree / "frontend"
    if not (frontend / "node_modules").is_dir():
        npm = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
        done = runner(
            [npm, "ci", "--no-audit", "--no-fund"], cwd=frontend, stdout=sys.stderr
        )
        if done.returncode != 0:
            return emit(
                [
                    {
                        "kind": "npm-ci",
                        "detail": f"npm ci failed (exit {done.returncode})",
                    }
                ]
            )
    argv = [
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(tree / "scripts" / "package.ps1"),
        "-Out",
        str(out),
        "-Python",
        sys.executable,
    ]
    done = runner(argv, cwd=tree, stdout=sys.stderr)
    if done.returncode != 0:
        return emit(
            [
                {
                    "kind": "package",
                    "detail": f"package.ps1 failed (exit {done.returncode})",
                }
            ]
        )
    return emit([])


def assets(tree: Path, out: Path, version: str) -> int:
    out = Path(out)
    repo = public_repo(tree)
    problems = []
    zip_path, installer = out / ZIP_NAME.format(v=version), out / "install.ps1"
    for path in (zip_path, installer):
        if not path.is_file():
            problems.append(
                {
                    "kind": "missing",
                    "path": path.name,
                    "detail": f"{path.name}: missing",
                }
            )
    if problems:
        return emit(problems)
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        if (
            "VERSION" not in names
            or archive.read("VERSION").decode().strip() != version
        ):
            problems.append(
                {
                    "kind": "zip-version",
                    "path": zip_path.name,
                    "detail": f"{zip_path.name}: VERSION is not {version}",
                }
            )
    text = installer.read_text(encoding="ascii", errors="replace")
    if "__RELEASES_REPO__" in text or f"'{repo}'" not in text:
        problems.append(
            {
                "kind": "installer-repo",
                "path": "install.ps1",
                "detail": f"install.ps1: releases repo is not {repo}",
            }
        )
    return emit(problems)


# --- smoke install -----------------------------------------------------------------------------


def installer_url(repo: str, version: str) -> str:
    return f"https://github.com/{repo}/releases/download/v{version}/install.ps1"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def seed_port(root: Path, port: int) -> None:
    """The launcher keeps the saved port when it is free; a fresh install starts there."""
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "launcher.json").write_text(json.dumps({"port": port}), encoding="utf-8")


def smoke_env(root: Path, environ: dict[str, str]) -> dict[str, str]:
    env = {
        k: v
        for k, v in environ.items()
        if not k.upper().startswith("AOC_")
        and not k.upper().endswith(("_API_KEY", "_TOKEN"))
    }
    env["PSModulePath"] = ""
    env["AOC_INSTALL_ROOT"] = str(root)
    env["AOC_DESKTOP_DIR"] = env["AOC_PROGRAMS_DIR"] = str(root / "shortcuts")
    env["AOC_UNINSTALL_SKIP_REGISTRY"] = "1"
    return env


def _get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as reply:
            return reply.status, reply.read().decode("utf-8", "replace")
    except OSError as exc:
        return 0, str(exc)


def _post(url: str, timeout: float = 10.0) -> int:
    request = urllib.request.Request(url, data=b"", method="POST", headers=HEADER)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.status
    except OSError:
        return 0


def wait_health(port: int, timeout: float = 120.0, get=_get, sleep=time.sleep) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, body = get(f"http://127.0.0.1:{port}/api/health")
        if status == 200 and '"answeroncue"' in body:
            return True
        sleep(1.0)
    return False


def wait_down(port: int, timeout: float = 20.0, get=_get, sleep=time.sleep) -> bool:
    """The app has stopped: /api/health no longer answers."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if get(f"http://127.0.0.1:{port}/api/health")[0] == 0:
            return True
        sleep(0.5)
    return False


def smoke_rows(
    root: Path,
    repo: str,
    version: str,
    *,
    port: int | None = None,
    runner=subprocess.run,
    get=_get,
    post=_post,
    download=urllib.request.urlretrieve,
    wait=wait_health,
    sleep=time.sleep,
    down=None,
) -> list[dict]:
    """The smoke steps in order; stop after the first failure. Rows {name, ok, detail}."""
    rows: list[dict] = []

    def row(name: str, ok: bool, detail: str = "") -> bool:
        rows.append({"name": name, "ok": ok, "detail": detail})
        return ok

    (root / "shortcuts").mkdir(parents=True, exist_ok=True)
    port = port or free_port()
    seed_port(root, port)
    installer, url = root / "install.ps1", installer_url(repo, version)
    try:
        download(url, str(installer))
        row("download install.ps1", True, url)
    except OSError as exc:
        row("download install.ps1", False, str(exc))
        return rows
    try:
        done = runner(
            [
                POWERSHELL,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(installer),
            ],
            env=smoke_env(root, dict(os.environ)),
            capture_output=True,
            text=True,
            timeout=3600,
        )
    except subprocess.TimeoutExpired:
        row("install", False, "timed out after 3600 s")
        return rows
    out = (done.stdout or "") + (done.stderr or "")
    tail = [line for line in out.splitlines() if "INSTALL" in line][
        -1:
    ] or out.strip().splitlines()[-1:]
    if not row("install", f"INSTALL OK {version}" in out, tail[0] if tail else ""):
        return rows
    # The installer starts the app: from here on, always ask it to stop, whatever fails.
    try:
        if row(f"/api/health on {port}", wait(port)):
            status, body = get(f"http://127.0.0.1:{port}/")
            row(
                "/ serves the interface",
                status == 200 and "<html" in body.lower(),
                f"HTTP {status}",
            )
    finally:
        status = post(f"http://127.0.0.1:{port}/api/system/shutdown")
        down = down or (lambda p: wait_down(p, get=get, sleep=sleep))
        row(
            "POST /api/system/shutdown",
            status in (200, 202) and down(port),
            f"HTTP {status}",
        )
    return rows


def smoke(tree: Path, version: str, folder: Path) -> int:
    root = Path(folder) / "aoc"
    rows = smoke_rows(root, public_repo(tree), version)
    passed = (
        all(r["ok"] for r in rows)
        and any(r["name"].startswith("/ serves") for r in rows)
        and rows[-1]["name"].startswith("POST")
    )
    problems = (
        []
        if passed
        else [
            {
                "kind": "smoke",
                "detail": next(
                    (f"{r['name']}: {r['detail']}" for r in rows if not r["ok"]),
                    "the smoke install stopped early",
                ),
            }
        ]
    )
    return emit(
        problems,
        warnings=[f"Folder to delete when done (after the app has stopped): {root}"],
        data={"rows": rows},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AnswerOnCue's release hooks.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audio")
    p = sub.add_parser("build")
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("assets")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--version", required=True)
    p = sub.add_parser("smoke")
    p.add_argument("--version", required=True)
    p.add_argument("--folder", type=Path, required=True)
    args = parser.parse_args(argv)
    tree = Path.cwd()
    try:
        if args.command == "audio":
            return audio(tree)
        if args.command == "build":
            return build(tree, args.out)
        if args.command == "assets":
            return assets(tree, args.out, args.version)
        return smoke(tree, args.version, args.folder)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        return emit([], error=True, data={"error": f"{exc.__class__.__name__}: {exc}"})


if __name__ == "__main__":
    sys.exit(main())
