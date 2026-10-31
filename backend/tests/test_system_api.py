import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.session import SessionLocal
from app.features.jobs.models import AiJob
from app.features.library.models import Subject
from app.features.system import router as system_router
from app.features.system import service
from app.features.system.version import app_version
from app.main import create_app

BACKEND = Path(__file__).resolve().parents[1]
GUARD = {"X-AnswerOnCue": "1", "Host": "127.0.0.1:8770"}


def upgrade(url: str) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for name in ("AOC_RELEASE_SOURCE", "AOC_SERVER_PORT", "AOC_INSTALL_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AOC_RELEASES_REPO", "owner/releases")
    service.reset_cache()
    yield
    service.reset_cache()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", frontend_dist=tmp_path / "dist")


@pytest.fixture
def api(settings) -> TestClient:
    upgrade(settings.effective_database_url)
    return TestClient(create_app(settings))


@pytest.fixture
def root(monkeypatch, tmp_path) -> Path:
    path = tmp_path / "Zażółć gęślą jaźń" / "AnswerOnCue"
    path.mkdir(parents=True)
    monkeypatch.setenv("AOC_INSTALL_ROOT", str(path))
    return path


class Github:
    def __init__(self, monkeypatch, status=200, tag="v0.2.0", error=None):
        self.calls: list[httpx2.Request] = []
        self.status, self.tag, self.error = status, tag, error
        monkeypatch.setattr(system_router, "http_client_factory", self.client)

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.calls.append(request)
        if self.error:
            raise self.error
        if request.url.path.endswith("install.ps1"):
            return httpx2.Response(200, content=b"# installer")
        return httpx2.Response(self.status, json={"tag_name": self.tag})

    def client(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self.handler))


def test_info_not_installed_makes_no_request(api, monkeypatch):
    def refuse(request):
        raise AssertionError("network used")

    monkeypatch.setattr(
        system_router,
        "http_client_factory",
        lambda: httpx2.Client(transport=httpx2.MockTransport(refuse)),
    )
    data = api.get("/api/system/info?refresh=true").json()
    assert data["installed"] is False
    assert data["latest"] is None and data["update_available"] is False
    assert data["check_message"] == "Updates only work in the installed version."
    assert data["version"] == app_version()
    assert data["running_jobs"] == 0 and data["last_update"] is None


def test_installed_newer_version(api, root, monkeypatch):
    github = Github(monkeypatch, tag="v0.2.0")
    data = api.get("/api/system/info").json()
    assert data["installed"] is True
    assert data["latest"] == "0.2.0" and data["update_available"] is True
    assert data["check_message"] is None and data["checked_at"]
    request = github.calls[0]
    assert str(request.url) == "https://api.github.com/repos/owner/releases/releases/latest"
    assert request.headers["accept"] == "application/vnd.github+json"


def test_installed_same_version(api, root, monkeypatch):
    Github(monkeypatch, tag="v0.1.0")
    data = api.get("/api/system/info").json()
    assert data["latest"] == "0.1.0" and data["update_available"] is False


def test_installed_no_release_404(api, root, monkeypatch):
    Github(monkeypatch, status=404)
    data = api.get("/api/system/info").json()
    assert data["latest"] is None
    assert data["check_message"] == "No version has been published yet."


def test_network_error_and_other_status(api, root, monkeypatch):
    Github(monkeypatch, error=httpx2.ConnectError("down"))
    data = api.get("/api/system/info").json()
    assert data["check_message"] == "Could not check for updates (no connection)."
    Github(monkeypatch, status=500)
    data = api.get("/api/system/info").json()
    assert data["check_message"] == "Could not check for updates."


def test_local_release_source(api, root, monkeypatch, tmp_path):
    source = tmp_path / "Releases"
    source.mkdir()
    for version in ("0.9.9", "0.10.0", "0.2.0"):
        (source / f"answeroncue-{version}.zip").write_bytes(b"x")
    monkeypatch.setenv("AOC_RELEASE_SOURCE", str(source))
    github = Github(monkeypatch)
    data = api.get("/api/system/info").json()
    assert data["latest"] == "0.10.0" and data["update_available"] is True
    assert github.calls == []


def test_cache_within_six_hours_and_refresh(api, root, monkeypatch, settings):
    github = Github(monkeypatch)
    api.get("/api/system/info")
    api.get("/api/system/info")
    assert len(github.calls) == 1
    api.get("/api/system/info?refresh=true")
    assert len(github.calls) == 2
    # a restart (empty memory) still uses the file cache
    service.reset_cache()
    api.get("/api/system/info")
    assert len(github.calls) == 2
    # an old file cache is ignored
    service.reset_cache()
    path = settings.data_dir / "update-check.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["checked_at"] = (datetime.now(UTC) - timedelta(hours=7)).isoformat()
    path.write_text(json.dumps(raw), encoding="utf-8")
    api.get("/api/system/info")
    assert len(github.calls) == 3


def test_running_jobs_counts_live_jobs_only(api, settings):
    with SessionLocal(settings.effective_database_url) as session:
        subject = Subject(name="Historia")
        session.add(subject)
        session.commit()
        old = datetime.now(UTC) - timedelta(hours=2)
        for status, updated in (
            ("running", datetime.now(UTC)),
            ("queued", datetime.now(UTC)),
            ("running", old),
            ("done", datetime.now(UTC)),
        ):
            job = AiJob(
                kind="k",
                subject_id=subject.id,
                status=status,
                steps_total=1,
                steps_done=0,
                params_json="{}",
                state_json="{}",
            )
            session.add(job)
            session.flush()
            job.updated_at = updated
        session.commit()
    assert api.get("/api/system/info").json()["running_jobs"] == 2


def test_last_update_from_status_file(api, settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "update-status.json").write_text(
        json.dumps(
            {
                "ok": False,
                "version": "0.2.0",
                "from_version": "0.1.0",
                "message": "Migration failed.",
                "at": "2026-10-21T10:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8-sig",
    )
    last = api.get("/api/system/info").json()["last_update"]
    assert last["ok"] is False and last["message"] == "Migration failed."
    assert last["version"] == "0.2.0" and last["from_version"] == "0.1.0"


def test_shutdown_guarded(api, monkeypatch):
    exits = []
    monkeypatch.setattr(system_router, "exit_process", lambda: exits.append(1))
    assert api.post("/api/system/shutdown").status_code == 403
    assert api.post("/api/system/shutdown", headers={"Host": "127.0.0.1:8770"}).status_code == 403
    evil = {"X-AnswerOnCue": "1", "Host": "evil.example:8770"}
    assert api.post("/api/system/shutdown", headers=evil).status_code == 403
    cross = {**GUARD, "Origin": "https://evil.example"}
    assert api.post("/api/system/shutdown", headers=cross).status_code == 403
    assert exits == []
    ok = api.post("/api/system/shutdown", headers=GUARD)
    assert ok.status_code == 202 and ok.json() == {"message": "The program is shutting down."}
    assert exits == [1]
    localhost = {**GUARD, "Host": "localhost:8770", "Origin": "http://localhost:8770"}
    assert api.post("/api/system/shutdown", headers=localhost).status_code == 202


def test_shutdown_host_port_must_match_server_port(api, monkeypatch):
    monkeypatch.setattr(system_router, "exit_process", lambda: None)
    monkeypatch.setenv("AOC_SERVER_PORT", "8771")
    assert api.post("/api/system/shutdown", headers=GUARD).status_code == 403
    headers = {**GUARD, "Host": "127.0.0.1:8771"}
    assert api.post("/api/system/shutdown", headers=headers).status_code == 202


def test_update_requires_guard(api, root, monkeypatch):
    Github(monkeypatch)
    assert api.post("/api/system/update").status_code == 403


def test_update_409_cases(api, root, monkeypatch, settings):
    spawned = []
    monkeypatch.setattr(system_router, "spawn", lambda *a, **k: spawned.append(a))
    monkeypatch.delenv("AOC_INSTALL_ROOT")
    response = api.post("/api/system/update", headers=GUARD)
    assert response.status_code == 409
    assert response.json()["detail"] == "Updating only works in the installed version."

    monkeypatch.setenv("AOC_INSTALL_ROOT", str(root))
    Github(monkeypatch, tag="v0.1.0")
    response = api.post("/api/system/update", headers=GUARD)
    assert (
        response.status_code == 409
        and response.json()["detail"] == "You already have the latest version."
    )

    service.reset_cache()
    (settings.data_dir / "update-check.json").unlink()
    Github(monkeypatch, tag="v0.2.0")
    (root / "updates").mkdir()
    lock = root / "updates" / "update.lock"
    lock.write_text("1", encoding="utf-8")
    response = api.post("/api/system/update", headers=GUARD)
    assert (
        response.status_code == 409
        and response.json()["detail"] == "An update is already in progress."
    )
    assert spawned == []
    # a stale lock (older than 15 minutes) does not block
    old = (datetime.now(UTC) - timedelta(minutes=20)).timestamp()
    os.utime(lock, (old, old))
    assert api.post("/api/system/update", headers=GUARD).status_code == 202


def test_update_happy_path(api, root, monkeypatch):
    github = Github(monkeypatch, tag="v0.2.0")
    monkeypatch.setenv("AOC_SERVER_PORT", "8770")
    monkeypatch.setenv("PSModulePath", r"C:\Program Files\PowerShell\Modules")
    calls = []
    monkeypatch.setattr(system_router, "spawn", lambda *a, **k: calls.append((a, k)))
    exits = []
    monkeypatch.setattr(system_router, "exit_process", lambda: exits.append(1))
    response = api.post("/api/system/update", headers=GUARD)
    assert response.status_code == 202 and response.json() == {"version": "0.2.0"}
    installer = root / "updates" / "install-0.2.0.ps1"
    assert installer.read_text(encoding="utf-8") == "# installer"
    assert (root / "updates" / "update.lock").exists()
    ((args, kwargs),) = calls
    assert args[0][:7] == [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-WindowStyle",
        "Hidden",
        "-File",
    ]
    assert args[0][7] == str(installer)
    env = kwargs["env"]
    assert env["AOC_UPDATE"] == "1" and env["AOC_WAIT_PID"] == str(os.getpid())
    assert env["AOC_SERVER_PORT"] == "8770" and env["AOC_INSTALL_ROOT"] == str(root)
    assert "AOC_RELEASE_SOURCE" not in env
    # PowerShell 5.1's own module path: a PowerShell 7 entry breaks it, none hides Get-FileHash.
    assert env["PSModulePath"] == service.windows_powershell_module_path()
    assert "PowerShell\\Modules" not in env["PSModulePath"].replace("WindowsPowerShell", "")
    # A detached powershell.exe (no console) exits 0 without running the script.
    assert not kwargs["creationflags"] & getattr(subprocess, "DETACHED_PROCESS", 0x8)
    assert exits == []
    assert github.calls[-1].url.path == "/owner/releases/releases/download/v0.2.0/install.ps1"


def test_update_from_release_source(api, root, monkeypatch, tmp_path):
    source = tmp_path / "Releases"
    source.mkdir()
    (source / "answeroncue-0.3.0.zip").write_bytes(b"x")
    (source / "install.ps1").write_text("# local installer", encoding="utf-8")
    monkeypatch.setenv("AOC_RELEASE_SOURCE", str(source))
    calls = []
    monkeypatch.setattr(system_router, "spawn", lambda *a, **k: calls.append((a, k)))
    assert api.post("/api/system/update", headers=GUARD).status_code == 202
    ((args, kwargs),) = calls
    assert kwargs["env"]["AOC_RELEASE_SOURCE"] == str(source)
    assert Path(args[0][7]).read_text(encoding="utf-8") == "# local installer"
