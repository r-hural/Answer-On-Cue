import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from distribution import postinstall

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def windows_powershell_modules(monkeypatch):
    """A PowerShell 7 parent leaks its module path; PowerShell 5.1 then lacks Get-FileHash."""
    monkeypatch.delenv("PSModulePath", raising=False)


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "Zażółć gęślą jaźń" / "AnswerOnCue"
    path.mkdir(parents=True)
    monkeypatch.setenv("AOC_INSTALL_ROOT", str(path))
    monkeypatch.setenv("AOC_DESKTOP_DIR", str(tmp_path / "Desktop"))
    monkeypatch.setenv("AOC_PROGRAMS_DIR", str(tmp_path / "Programs"))
    monkeypatch.delenv("AOC_RELEASE_SOURCE", raising=False)
    monkeypatch.delenv("AOC_UNINSTALL_SKIP_REGISTRY", raising=False)
    return path


def make_db(data_dir: Path, rows: int = 1) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    database = data_dir / "answeroncue.db"
    with sqlite3.connect(database) as connection:
        connection.execute("create table t (x text)")
        for index in range(rows):
            connection.execute("insert into t values (?)", (f"wiersz {index}",))
    return database


def ok(*args, **kwargs):
    return subprocess.CompletedProcess(args, 0, "", "")


def test_backup_keeps_three(root):
    data = root / "data"
    make_db(data)
    paths = [postinstall.backup_database(data, "0.1.0") for _ in range(5)]
    kept = sorted((data / "backups").glob("*.db"))
    assert len(kept) == 3
    assert kept == sorted(p for p in paths[-3:] if p)
    assert kept[0].name.startswith("answeroncue-0.1.0-")
    assert postinstall.backup_database(root / "nodata", None) is None


def test_failed_migration_restores_backup_and_reports(root):
    data = root / "data"
    database = make_db(data)
    (data / "launcher.json").write_text(
        json.dumps({"port": 8770, "version": "0.1.0", "previous": None}), encoding="utf-8"
    )

    def broken_run(*args, **kwargs):
        database.write_bytes(b"corrupted by a half-done migration")
        return subprocess.CompletedProcess(args, 1, "", "boom: no such column")

    code = postinstall.main(["--version", "0.1.1", "--update"], run=broken_run)
    assert code == 2
    backups = list((data / "backups").glob("*.db"))
    assert len(backups) == 1
    assert database.read_bytes() == backups[0].read_bytes()
    with sqlite3.connect(database) as connection:
        assert connection.execute("select count(*) from t").fetchone() == (1,)
    status = json.loads((data / "update-status.json").read_text(encoding="utf-8"))
    assert status["ok"] is False
    assert status["version"] == "0.1.1" and status["from_version"] == "0.1.0"
    assert "boom" in status["message"]
    state = json.loads((data / "launcher.json").read_text(encoding="utf-8"))
    assert state["version"] == "0.1.0"


def test_real_migration_on_temp_data_dir(root):
    data = root / "data"
    env = dict(os.environ, AOC_DATA_DIR=str(data), PYTHONUTF8="1")
    postinstall.migrate(BACKEND, env)
    with sqlite3.connect(data / "answeroncue.db") as connection:
        tables = {r[0] for r in connection.execute("select name from sqlite_master")}
    assert {"alembic_version", "subject"} <= tables


def test_migrate_failure_raises(root):
    def bad(*args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "kaput")

    with pytest.raises(postinstall.PostinstallError, match="kaput"):
        postinstall.migrate(BACKEND, {}, bad)


def test_voice_failure_is_warning(root):
    def bad(*args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "no network")

    def boom(*args, **kwargs):
        raise OSError("gone")

    warning = postinstall.ensure_voice(BACKEND, {}, root / "data", bad)
    assert warning and "pl_PL-bass-high" in warning
    assert postinstall.ensure_voice(BACKEND, {}, root / "data", boom)


def test_voice_skipped_when_present(root):
    voices = root / "data" / "voices"
    voices.mkdir(parents=True)
    (voices / "pl_PL-bass-high.onnx").write_bytes(b"x")
    calls = []

    def spy(*args, **kwargs):
        calls.append(args)
        return ok()

    assert postinstall.ensure_voice(BACKEND, {}, root / "data", spy) is None
    assert calls == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shortcuts")
def test_shortcuts_created_with_polish_names(root, tmp_path):
    # Ł is in no Cyrillic or Western ANSI code page and Ж in no Central European one.
    desktop, programs = tmp_path / "Desktop Łukasz Ж", tmp_path / "Programs"
    backend = root / "app" / "0.1.0" / "backend"
    made = postinstall.write_shortcuts(backend, desktop, programs, root)
    assert [p.name for p in made] == [
        "AnswerOnCue.lnk",
        "AnswerOnCue.lnk",
        "Uninstall AnswerOnCue.lnk",
    ]
    assert all(p.exists() for p in made)
    assert made[1].parent.name == "AnswerOnCue"
    # Shell.Application reads the link with a Unicode path (WScript.Shell would mangle it).
    probe = (
        "$s = (New-Object -ComObject Shell.Application).NameSpace($env:AOC_DIR)"
        ".ParseName($env:AOC_NAME).GetLink;"
        " [Console]::OutputEncoding = [Text.Encoding]::UTF8;"
        " $s.Path; $s.Arguments; $s.WorkingDirectory"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", probe],
        capture_output=True,
        env=dict(os.environ, AOC_DIR=str(made[0].parent), AOC_NAME=made[0].name),
        check=True,
    )
    target, arguments, workdir = result.stdout.decode("utf-8").splitlines()[:3]
    assert target == str(backend.parent / ".venv" / "Scripts" / "pythonw.exe")
    assert arguments == "-m distribution.launcher"
    assert workdir == str(backend)


def test_shortcut_failure_is_postinstall_error(root, tmp_path):
    def broken(*args):
        raise OSError("access denied")

    with pytest.raises(postinstall.PostinstallError, match="access denied"):
        postinstall.write_shortcuts(root, tmp_path / "Desktop", tmp_path / "Programs", root, broken)


def test_uninstall_entry_values(root):
    seen = {}
    postinstall.register_uninstall(
        root, "0.1.0", lambda key, values: seen.update(key=key, **values)
    )
    assert seen["key"].endswith(r"\Uninstall\AnswerOnCue")
    assert seen["DisplayName"] == "AnswerOnCue"
    assert seen["DisplayVersion"] == "0.1.0"
    assert seen["InstallLocation"] == str(root)
    assert str(root / "uninstall.ps1") in seen["UninstallString"]


def test_prune_keeps_current_and_previous_and_drops_partial(root):
    app = root / "app"
    for name, complete in [("0.1.0", True), ("0.1.1", True), ("0.0.9", True), ("0.2.0", False)]:
        (app / name).mkdir(parents=True)
        if complete:
            (app / name / ".complete").write_text("")
    (app / "0.1.2.partial").mkdir()
    removed = postinstall.prune_versions(root, {"0.1.1", "0.1.0"})
    assert sorted(removed) == ["0.0.9", "0.1.2.partial", "0.2.0"]
    assert sorted(p.name for p in app.iterdir()) == ["0.1.0", "0.1.1"]


def test_keep_set_member_without_complete_survives(root):
    (root / "app" / "0.1.1").mkdir(parents=True)
    assert postinstall.prune_versions(root, {"0.1.1"}) == []


def test_launcher_state_and_release_source_saved(root, tmp_path):
    data = root / "data"
    data.mkdir(parents=True)
    (data / "launcher.json").write_text(
        json.dumps({"port": 8775, "version": "0.1.0", "previous": None}), encoding="utf-8"
    )
    seen = {}
    source = str(tmp_path / "Wydania")
    code = postinstall.main(
        ["--version", "0.1.1", "--release-source", source],
        run=ok,
        registry_writer=lambda key, values: seen.update(values),
    )
    assert code == 0
    state = json.loads((data / "launcher.json").read_text(encoding="utf-8"))
    assert state == {
        "port": 8775,
        "version": "0.1.1",
        "previous": "0.1.0",
        "release_source": source,
    }
    assert seen["DisplayVersion"] == "0.1.1"
    status = json.loads((data / "update-status.json").read_text(encoding="utf-8"))
    assert status["ok"] is True and status["from_version"] == "0.1.0"


def test_old_vc_runtime_is_a_warning_not_a_failure(root, capsys):
    message = "Piper voices need a newer Microsoft Visual C++ Redistributable."
    code = postinstall.main(
        ["--version", "0.1.2"],
        run=ok,
        registry_writer=lambda key, values: None,
        runtime_check=lambda: message,
    )
    assert code == 0
    assert f"WARNING: {message}" in capsys.readouterr().out
    status = json.loads((root / "data" / "update-status.json").read_text(encoding="utf-8"))
    assert status["ok"] is True and status["message"] == message


def test_vc_runtime_check_that_breaks_never_fails_install():
    def boom():
        raise OSError("no version.dll")

    assert postinstall.vc_runtime_warning(boom) is None
    assert postinstall.vc_runtime_warning(lambda: None) is None
