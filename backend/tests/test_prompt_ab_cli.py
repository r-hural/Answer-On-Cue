"""The A/B command line: help, and a refusal that reads no data folder."""

import os
import subprocess
import sys

from test_topics_support import BACKEND


def cli(*args: str, data_dir) -> subprocess.CompletedProcess:
    env = {**os.environ, "AOC_DATA_DIR": str(data_dir), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "app.features.prompt_ab", *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def test_help(tmp_path):
    done = cli("--help", data_dir=tmp_path / "data")
    assert done.returncode == 0, done.stderr
    assert "--old-ref" in done.stdout and "--dry-run" in done.stdout


def test_missing_database_refused(tmp_path):
    data = tmp_path / "data"
    done = cli("--db", str(tmp_path / "missing.db"), "--old-ref", "HEAD", data_dir=data)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "Refused: No such database file" in done.stderr
    assert not list(tmp_path.rglob("*.db"))
