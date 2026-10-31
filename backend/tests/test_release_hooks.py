from __future__ import annotations

import importlib.util
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "release_hooks", ROOT / "scripts" / "release_hooks.py"
)
hooks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hooks)

REPO = "r-hural/Answer-On-Cue"


def _out(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "release.json").write_text(json.dumps({"repo": REPO}), encoding="utf-8")
    return tmp_path


# --- audio provenance --------------------------------------------------------------------------


def _fixture(folder: Path, voice: str | None = "Microsoft David Desktop") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a.wav").write_bytes(b"RIFF-synthetic")
    manifest = {"files": {"a.wav": {}}}
    if voice:
        manifest["sapi_voice"] = voice
    (folder / "fixtures.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder / "a.wav"


def test_audio_declared_synthetic_passes(tmp_path, capsys):
    _fixture(tmp_path / "backend" / "tests" / "fixtures" / "audio")
    # a copy elsewhere (the e2e fixtures) is matched by its content
    (tmp_path / "frontend" / "e2e" / "fixtures").mkdir(parents=True)
    (tmp_path / "frontend" / "e2e" / "fixtures" / "a.wav").write_bytes(b"RIFF-synthetic")
    assert hooks.audio(tmp_path) == 0
    rows = _out(capsys)["data"]["audio"]
    assert len(rows) == 2 and all("synthetic" in r["provenance"] for r in rows)


@pytest.mark.parametrize("suffix", [".wav", ".webm", ".mp4", ".flac"])
def test_undeclared_audio_and_video_fail(tmp_path, capsys, suffix):
    (tmp_path / f"voice{suffix}").write_bytes(b"real")
    assert hooks.audio(tmp_path) == 1
    problem = _out(capsys)["problems"][0]
    assert problem["kind"] == "audio-provenance" and problem["path"] == f"voice{suffix}"


def test_audio_manifest_without_a_voice_does_not_count(tmp_path, capsys):
    _fixture(tmp_path, voice=None)
    assert hooks.audio(tmp_path) == 1


def test_real_audio_fixtures_are_declared_synthetic():
    known = hooks.synthetic_audio(ROOT / "backend" / "tests" / "fixtures")
    files = [
        p
        for folder in ("backend/tests/fixtures", "frontend/e2e/fixtures")
        for _, p in hooks.tree_files(ROOT / folder)
        if p.suffix.casefold() in hooks.AUDIO_SUFFIXES
    ]
    assert files and all(hooks._sha(p) in known for p in files)


# --- assets ------------------------------------------------------------------------------------


def _assets(
    out: Path, version: str = "0.1.0", zip_version: str = "0.1.0", repo: str = REPO
) -> None:
    out.mkdir(parents=True)
    with zipfile.ZipFile(out / f"answeroncue-{version}.zip", "w") as archive:
        archive.writestr("VERSION", zip_version + "\n")
    (out / "install.ps1").write_text(f"$ReleasesRepo = '{repo}'\n", encoding="ascii")


def test_assets_ok(tmp_path, capsys):
    _assets(tmp_path / "out")
    assert hooks.assets(_tree(tmp_path), tmp_path / "out", "0.1.0") == 0


def test_assets_missing(tmp_path, capsys):
    (tmp_path / "out").mkdir()
    assert hooks.assets(_tree(tmp_path), tmp_path / "out", "0.1.0") == 1
    assert {p["kind"] for p in _out(capsys)["problems"]} == {"missing"}


def test_assets_wrong_version_and_repo(tmp_path, capsys):
    _assets(tmp_path / "out", zip_version="0.0.9", repo="__RELEASES_REPO__")
    assert hooks.assets(_tree(tmp_path), tmp_path / "out", "0.1.0") == 1
    assert {p["kind"] for p in _out(capsys)["problems"]} == {"zip-version", "installer-repo"}


# --- build -------------------------------------------------------------------------------------


def test_build_runs_npm_ci_then_package(tmp_path, capsys):
    calls = []

    def runner(argv, **kw):
        calls.append((argv, kw["cwd"]))
        return subprocess.CompletedProcess(argv, 0)

    (tmp_path / "frontend").mkdir()
    assert hooks.build(tmp_path, tmp_path / "out", runner=runner) == 0
    assert "ci" in calls[0][0] and calls[0][1] == tmp_path / "frontend"
    assert calls[1][0][-4:-2] == ["-Out", str(tmp_path / "out")] and calls[1][1] == tmp_path


def test_build_reports_a_failure(tmp_path, capsys):
    (tmp_path / "frontend" / "node_modules").mkdir(parents=True)
    runner = lambda argv, **kw: subprocess.CompletedProcess(argv, 3)  # noqa: E731
    assert hooks.build(tmp_path, tmp_path / "out", runner=runner) == 1
    assert _out(capsys)["problems"][0]["kind"] == "package"


# --- smoke install -----------------------------------------------------------------------------


def test_installer_url():
    assert hooks.installer_url(REPO, "0.1.0") == (
        "https://github.com/r-hural/Answer-On-Cue/releases/download/v0.1.0/install.ps1"
    )


def test_smoke_env_is_a_strangers(tmp_path):
    env = hooks.smoke_env(
        tmp_path,
        {
            "AOC_OPENAI_API_KEY": "x",
            "AOC_DATA_DIR": "d",
            "GH_TOKEN": "t",
            "OPENAI_API_KEY": "y",
            "PATH": "p",
            "PSModulePath": "m",
        },
    )
    assert env["PATH"] == "p" and env["PSModulePath"] == ""
    assert not {"AOC_OPENAI_API_KEY", "OPENAI_API_KEY", "AOC_DATA_DIR", "GH_TOKEN"} & set(env)
    assert env["AOC_INSTALL_ROOT"] == str(tmp_path)
    assert env["AOC_DESKTOP_DIR"] == env["AOC_PROGRAMS_DIR"] == str(tmp_path / "shortcuts")
    assert env["AOC_UNINSTALL_SKIP_REGISTRY"] == "1"


def test_seed_port(tmp_path):
    hooks.seed_port(tmp_path, 8899)
    assert json.loads((tmp_path / "data" / "launcher.json").read_text(encoding="utf-8")) == {
        "port": 8899
    }


def _smoke(tmp_path, *, install_out="INSTALL OK 0.1.0", healthy=True, shutdown=200, down=True):
    posted = []

    def post(url):
        posted.append(url)
        return shutdown

    rows = hooks.smoke_rows(
        tmp_path / "aoc",
        REPO,
        "0.1.0",
        port=8899,
        runner=lambda argv, **kw: subprocess.CompletedProcess(argv, 0, install_out, ""),
        get=lambda url: (200, "<html>"),
        post=post,
        download=lambda url, path: None,
        wait=lambda port: healthy,
        down=lambda port: down,
    )
    return rows, posted


def test_smoke_passes(tmp_path):
    rows, posted = _smoke(tmp_path)
    assert [r["ok"] for r in rows] == [True] * 5 and posted
    assert (tmp_path / "aoc" / "shortcuts").is_dir()


def test_smoke_stops_after_a_failed_install(tmp_path):
    rows, posted = _smoke(tmp_path, install_out="INSTALL FAILED")
    assert [r["name"] for r in rows] == ["download install.ps1", "install"] and not posted


def test_smoke_sends_shutdown_when_health_never_answers(tmp_path):
    rows, posted = _smoke(tmp_path, healthy=False)
    assert posted and rows[-1]["name"].startswith("POST") and not rows[-2]["ok"]


def test_smoke_shutdown_must_stop_the_app(tmp_path):
    rows, _ = _smoke(tmp_path, down=False)
    assert not rows[-1]["ok"]


def test_wait_down():
    replies = iter([(200, ""), (0, "refused")])
    assert hooks.wait_down(1, get=lambda url: next(replies), sleep=lambda s: None)
