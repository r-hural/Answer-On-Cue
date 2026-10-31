import os
import subprocess
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import httpx2
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings, get_settings
from app.db.session import SessionDep
from app.features.jobs.models import AiJob
from app.features.jobs.service import is_live
from app.features.system import service
from app.features.system.version import app_version, releases_repo
from app.local_requests import require_local_request

router = APIRouter(prefix="/system", tags=["system"])

# Tests replace these hooks: a MockTransport client, and no real spawn or process exit.
http_client_factory: Callable[[], httpx2.Client | None] = lambda: None  # noqa: E731


def _spawn(command: list[str], **kwargs: Any) -> object:
    return subprocess.Popen(command, **kwargs)


def _exit_process() -> None:
    time.sleep(0.5)  # let the response reach the browser
    os._exit(0)


spawn: Callable[..., object] = _spawn
exit_process: Callable[[], None] = _exit_process

SettingsDep = Annotated[Settings, Depends(get_settings)]


class UpdateStatus(BaseModel):
    ok: bool
    version: str
    from_version: str | None
    message: str | None
    at: datetime


class SystemInfo(BaseModel):
    version: str
    installed: bool
    latest: str | None
    update_available: bool
    checked_at: datetime | None
    check_message: str | None
    running_jobs: int
    last_update: UpdateStatus | None


def _running_jobs(session: SessionDep) -> int:
    try:
        jobs = session.scalars(select(AiJob).where(AiJob.status.in_(("queued", "running")))).all()
    except SQLAlchemyError:
        return 0
    return sum(1 for job in jobs if is_live(job))


@router.get("/info", response_model=SystemInfo)
def info(session: SessionDep, settings: SettingsDep, refresh: bool = False) -> SystemInfo:
    version = app_version()
    result = service.check_for_update(
        settings.data_dir, releases_repo(), http_client_factory(), refresh
    )
    status = service.read_update_status(settings.data_dir)
    return SystemInfo(
        version=version,
        installed=service.install_root() is not None,
        latest=result.latest,
        update_available=service.update_available(version, result.latest),
        checked_at=result.checked_at,
        check_message=result.message,
        running_jobs=_running_jobs(session),
        last_update=UpdateStatus(**status) if status else None,
    )


@router.post("/shutdown", status_code=202, dependencies=[Depends(require_local_request)])
def shutdown(background: BackgroundTasks) -> dict[str, str]:
    background.add_task(lambda: exit_process())
    return {"message": "The program is shutting down."}


@router.post("/update", status_code=202, dependencies=[Depends(require_local_request)])
def update(request: Request, settings: SettingsDep) -> dict[str, str]:
    root = service.install_root()
    if root is None:
        raise HTTPException(409, service.NOT_INSTALLED_UPDATE)
    version = app_version()
    repo = releases_repo()
    result = service.check_for_update(settings.data_dir, repo, http_client_factory(), False)
    if result.latest is None or not service.update_available(version, result.latest):
        raise HTTPException(409, service.UP_TO_DATE)
    if service.lock_is_fresh(root):
        raise HTTPException(409, service.UPDATE_RUNNING)
    lock = service.write_lock(root)
    try:
        installer = service.fetch_installer(root, repo, result.latest, http_client_factory())
    except (httpx2.HTTPError, OSError) as exc:
        lock.unlink(missing_ok=True)
        raise HTTPException(502, service.FETCH_ERROR) from exc
    env = dict(os.environ)
    env.update(
        AOC_UPDATE="1",
        AOC_WAIT_PID=str(os.getpid()),
        AOC_SERVER_PORT=os.environ.get("AOC_SERVER_PORT") or str(request.url.port or ""),
        AOC_INSTALL_ROOT=str(root),
    )
    source = service.release_source()
    if source:
        env["AOC_RELEASE_SOURCE"] = str(source)
    launch_installer(installer, env)
    return {"version": result.latest}


def launch_installer(installer: Path, env: dict[str, str]) -> None:
    """Starts the installer in a hidden console that outlives this server process."""
    # Without a console (DETACHED_PROCESS) powershell.exe exits 0 and never runs the script.
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "CREATE_NO_WINDOW", 0
    )
    spawn(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(installer),
        ],
        env={**env, "PSModulePath": service.windows_powershell_module_path()},
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
