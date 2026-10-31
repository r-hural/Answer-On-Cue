import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from distribution.package import build_package

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
VERSION = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


@pytest.fixture(autouse=True)
def windows_powershell_modules(monkeypatch):
    """A PowerShell 7 parent leaks its module path; PowerShell 5.1 then lacks Get-FileHash."""
    monkeypatch.delenv("PSModulePath", raising=False)


OUR_SCRIPTS = ["install.ps1", "uninstall.ps1", "package.ps1"]


def powershell(script: Path, env: dict[str, str], timeout: int = 180):
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        env={**os.environ, **env},
        timeout=timeout,
    )
    return result.returncode, result.stdout.decode("utf-8", "replace") + result.stderr.decode(
        "utf-8", "replace"
    )


@pytest.fixture(scope="module")
def dist(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("dist")
    (path / "assets").mkdir()
    (path / "index.html").write_text("<html></html>", encoding="utf-8")
    return path


@pytest.fixture
def release(tmp_path, dist) -> Path:
    out = tmp_path / "Wydania"
    build_package(REPO_ROOT, dist, out)
    return out


@pytest.fixture
def env(tmp_path, release) -> dict[str, str]:
    root = tmp_path / "Zażółć gęślą jaźń" / "AnswerOnCue"
    return {
        "AOC_INSTALL_ROOT": str(root),
        "AOC_RELEASE_SOURCE": str(release),
        "AOC_DESKTOP_DIR": str(tmp_path / "Desktop"),
        "AOC_PROGRAMS_DIR": str(tmp_path / "Programs"),
        "AOC_UNINSTALL_SKIP_REGISTRY": "1",
        "AOC_INSTALL_DRY_RUN": "1",
        "AOC_UPDATE": "",
    }


def test_scripts_ascii_only():
    for name in OUR_SCRIPTS:
        path = SCRIPTS / name
        if path.exists():
            path.read_bytes().decode("ascii")


def test_scripts_parse():
    for name in OUR_SCRIPTS:
        path = SCRIPTS / name
        if not path.exists():
            continue
        command = (
            "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
            f"'{path}', [ref]$null, [ref]$e); "
            "if ($e) { $e | ForEach-Object { $_.Message }; exit 1 }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True
        )
        assert result.returncode == 0, f"{name}: {result.stdout}{result.stderr}"


def test_dry_run_from_local_source(env, release):
    code, output = powershell(release / "install.ps1", env)
    assert f"INSTALL DRY RUN OK {VERSION}" in output, output
    root = Path(env["AOC_INSTALL_ROOT"])
    assert (root / "app" / VERSION / "backend" / "app" / "main.py").is_file()
    assert not (root / "app" / VERSION / ".complete").exists()
    assert not (root / "app" / f"{VERSION}.partial").exists()
    assert "python install 3.13" in output and "pip install --python" in output
    assert not (root / "uv").exists()


def test_dry_run_bad_checksum(env, release):
    sha = release / f"answeroncue-{VERSION}.zip.sha256"
    sha.write_text("0" * 64 + f"  answeroncue-{VERSION}.zip\n", encoding="ascii")
    code, output = powershell(release / "install.ps1", env)
    assert "INSTALL FAILED" in output and "checksum mismatch" in output, output
    assert "INSTALL DRY RUN OK" not in output
    assert not (Path(env["AOC_INSTALL_ROOT"]) / "app" / VERSION).exists()


def test_dry_run_picks_newest_local_version(env, release, dist):
    build_package(REPO_ROOT, dist, release, "0.10.0")
    build_package(REPO_ROOT, dist, release, "0.9.9")
    code, output = powershell(release / "install.ps1", env)
    assert "INSTALL DRY RUN OK 0.10.0" in output, output
    assert (Path(env["AOC_INSTALL_ROOT"]) / "app" / "0.10.0").is_dir()


def test_uninstall_keeps_data(tmp_path):
    root = tmp_path / "Zażółć gęślą jaźń" / "AnswerOnCue"
    for name in ("app/0.1.0", "uv", "python", "cache/uv", "updates", "data/logs"):
        (root / name).mkdir(parents=True)
    (root / "data" / "answeroncue.db").write_bytes(b"x")
    (root / "uninstall.ps1").write_text("# copy", encoding="ascii")
    desktop, programs = tmp_path / "Desktop", tmp_path / "Programs"
    shortcut = desktop / "AnswerOnCue.lnk"
    menu = programs / "AnswerOnCue"
    desktop.mkdir()
    menu.mkdir(parents=True)
    shortcut.write_bytes(b"lnk")
    (menu / "AnswerOnCue.lnk").write_bytes(b"lnk")
    env = {
        "AOC_INSTALL_ROOT": str(root),
        "AOC_DESKTOP_DIR": str(desktop),
        "AOC_PROGRAMS_DIR": str(programs),
        "AOC_UNINSTALL_KEEP_DATA": "1",
        "AOC_UNINSTALL_SKIP_REGISTRY": "1",
    }
    code, output = powershell(SCRIPTS / "uninstall.ps1", env)
    assert "UNINSTALL OK" in output, output
    assert sorted(p.name for p in root.iterdir()) == ["data"]
    assert (root / "data" / "answeroncue.db").read_bytes() == b"x"
    assert not shortcut.exists() and not menu.exists()


def test_uninstall_removes_data_and_empty_root(tmp_path):
    root = tmp_path / "AnswerOnCue"
    (root / "data").mkdir(parents=True)
    env = {
        "AOC_INSTALL_ROOT": str(root),
        "AOC_DESKTOP_DIR": str(tmp_path / "d"),
        "AOC_PROGRAMS_DIR": str(tmp_path / "p"),
        "AOC_UNINSTALL_KEEP_DATA": "0",
        "AOC_UNINSTALL_SKIP_REGISTRY": "1",
    }
    code, output = powershell(SCRIPTS / "uninstall.ps1", env)
    assert "UNINSTALL OK" in output, output
    assert not root.exists()


@pytest.mark.skipif(os.environ.get("AOC_TEST_INSTALL") != "1", reason="real install, run by hand")
def test_real_install(tmp_path, release):
    real_dist = REPO_ROOT / "frontend" / "dist"
    assert (real_dist / "index.html").is_file(), "build the frontend first"
    build_package(REPO_ROOT, real_dist, release)
    root = tmp_path / "Zażółć gęślą jaźń" / "AnswerOnCue"
    env = {
        "AOC_INSTALL_ROOT": str(root),
        "AOC_RELEASE_SOURCE": str(release),
        "AOC_DESKTOP_DIR": str(tmp_path / "Desktop"),
        "AOC_PROGRAMS_DIR": str(tmp_path / "Programs"),
        "AOC_UNINSTALL_SKIP_REGISTRY": "1",
        "AOC_INSTALL_DRY_RUN": "",
        "AOC_UPDATE": "",
    }
    code, output = powershell(release / "install.ps1", env, timeout=1800)
    assert f"INSTALL OK {VERSION}" in output, output
    state = json.loads((root / "data" / "launcher.json").read_text(encoding="utf-8"))
    url = f"http://127.0.0.1:{state['port']}/api/health"
    health = None
    for _ in range(60):
        try:
            health = json.loads(urllib.request.urlopen(url, timeout=2).read())
            break
        except OSError:
            time.sleep(1)
    try:
        assert health and health["app"] == "answeroncue"
    finally:
        env.update(AOC_UNINSTALL_KEEP_DATA="0")
        code, output = powershell(root / "uninstall.ps1", env)
    assert "UNINSTALL OK" in output, output
    assert not root.exists()
