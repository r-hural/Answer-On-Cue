from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from app.config import Settings, get_settings
from app.db.session import SessionDep
from app.features.jobs import service
from app.features.jobs.schemas import JobOut
from app.features.providers.registry import GenerationDep

router = APIRouter(prefix="/jobs")

SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("")
def list_jobs(
    session: SessionDep,
    subject_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    live: bool = False,
) -> list[JobOut]:
    rows = service.list_jobs(session, subject_id, limit, live=live)
    return [service.job_out(job, names) for job, names in rows]


@router.get("/{job_id}")
def get_job(job_id: int, session: SessionDep) -> JobOut:
    job = service.get_job(session, job_id)
    return service.job_out(job, service.job_names(session, job))


@router.post("/{job_id}/resume", status_code=202)
def resume_job(
    job_id: int,
    session: SessionDep,
    settings: SettingsDep,
    generator: GenerationDep,
    background: BackgroundTasks,
) -> JobOut:
    job = service.prepare_resume(session, job_id)
    service.schedule(background, job, settings, generator)
    return service.job_out(job, service.job_names(session, job))
