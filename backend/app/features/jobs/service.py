"""Resumable AI jobs: a job is a fixed number of steps, each handled by a per-kind function.

A step handler adds its rows to the session and returns a dict; the runner stores that dict in
state_json["steps"][index] and commits both together, so a step is either fully saved or not at
all. On resume every step index already present is skipped, so no billable call is repeated.
"""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from app.config import Settings
from app.db.session import SessionLocal
from app.features.costs import estimates
from app.features.costs.spending import JOB_OVER_BUDGET, money
from app.features.jobs.models import AiJob
from app.features.jobs.schemas import JobOut, JobUsage
from app.features.library.models import Document, DocumentVersion, Subject
from app.features.providers.base import TextGenerator
from app.features.providers.types import ProviderError
from app.features.providers.usage import InMemoryUsageRecorder, get_usage_recorder

logger = logging.getLogger(__name__)

JOB_NOT_FOUND = "Task not found."
NOT_RESUMABLE = "This task cannot be resumed."
# The library's refusal of an older document version (topics.service.VERSION_REPLACED): a job
# fixed to a version that was replaced since cannot be resumed (its passages are no longer the
# ones topics link to).
VERSION_REPLACED = "A newer version of this document exists. Use the latest version."
UNEXPECTED_ERROR = "An unexpected error occurred while processing. Try resuming the task."

StepHandler = Callable[[Session, AiJob, int, TextGenerator], dict[str, Any]]
Summarizer = Callable[[AiJob], dict[str, Any]]

_handlers: dict[str, StepHandler] = {}
_summarizers: dict[str, Summarizer] = {}
# Jobs this process is running or has scheduled. Another process (the review CLI) may run jobs
# too, so a "queued"/"running" job unknown here counts as live until its row has not been
# touched for STALE_AFTER (every step commit touches it); after that it was cut off (restart,
# crash) and may be resumed. One step makes at most two AI calls of up to 120 s each.
STALE_AFTER = timedelta(minutes=10)
_active: set[int] = set()
_scheduled: set[int] = set()


class StepSkipped(Exception):
    """A step that could not be completed but does not stop the job; the job ends partial."""


def register_step_handler(kind: str, handler: StepHandler) -> None:
    _handlers[kind] = handler


def unregister_step_handler(kind: str) -> None:
    _handlers.pop(kind, None)


def register_summarizer(kind: str, summarizer: Summarizer) -> None:
    _summarizers[kind] = summarizer


def load_params(job: AiJob) -> dict[str, Any]:
    return json.loads(job.params_json)


def load_state(job: AiJob) -> dict[str, Any]:
    state = json.loads(job.state_json)
    state.setdefault("steps", {})
    return state


def step_results(job: AiJob) -> dict[int, dict[str, Any]]:
    return {int(k): v for k, v in load_state(job)["steps"].items()}


def create_job(
    session: Session,
    kind: str,
    subject_id: int,
    params: dict,
    steps_total: int,
    document_version_id: int | None = None,
) -> AiJob:
    job = AiJob(
        kind=kind,
        subject_id=subject_id,
        document_version_id=document_version_id,
        status="queued",
        steps_total=steps_total,
        steps_done=0,
        params_json=json.dumps(params, ensure_ascii=False),
        state_json=json.dumps({"steps": {}}),
    )
    session.add(job)
    session.commit()
    return job


def schedule(
    background: BackgroundTasks, job: AiJob, settings: Settings, generator: TextGenerator
) -> None:
    """Runs the job after the response; until then this process counts it as live.

    With a spending ceiling, a job whose estimate passes what is left is refused: stored as
    failed with the reason (so it stays resumable) and answered 409."""
    if settings.spending_ceiling_usd:
        _check_budget(job, settings)
    _scheduled.add(job.id)
    background.add_task(run_job, job.id, settings, generator)


def _check_budget(job: AiJob, settings: Settings) -> None:
    session = object_session(job)
    estimate = estimates.job_estimate(session, settings, job)
    if estimate.fits:
        return
    message = JOB_OVER_BUDGET.format(
        usd=money(estimate.usd),
        left=money(estimate.month.remaining_usd or 0.0),
        ceiling=money(estimate.month.ceiling_usd),
    )
    job.status = "failed"
    job.error_message = message
    session.commit()
    raise HTTPException(409, {"message": message, "estimate": estimate.model_dump(mode="json")})


def _stale(job: AiJob) -> bool:
    updated = job.updated_at
    if updated.tzinfo is not None:
        updated = updated.astimezone(UTC).replace(tzinfo=None)
    return datetime.now(UTC).replace(tzinfo=None) - updated > STALE_AFTER


def is_live(job: AiJob) -> bool:
    if job.status not in ("queued", "running"):
        return False
    return job.id in _active or job.id in _scheduled or not _stale(job)


def is_orphaned(job: AiJob) -> bool:
    return job.status in ("queued", "running") and not is_live(job)


def find_live_job(
    session: Session,
    kind: str,
    document_version_id: int | None = None,
    subject_id: int | None = None,
) -> AiJob | None:
    query = select(AiJob).where(AiJob.kind == kind, AiJob.status.in_(("queued", "running")))
    if document_version_id is not None:
        query = query.where(AiJob.document_version_id == document_version_id)
    if subject_id is not None:
        query = query.where(AiJob.subject_id == subject_id)
    return next((job for job in session.scalars(query) if is_live(job)), None)


def get_job(session: Session, job_id: int) -> AiJob:
    job = session.get(AiJob, job_id)
    if job is None:
        raise HTTPException(404, JOB_NOT_FOUND)
    return job


def job_usage(job: AiJob) -> list[JobUsage]:
    """Calls and tokens per provider/model, from the in-memory recorder (this process only)."""
    recorder = get_usage_recorder()
    if not isinstance(recorder, InMemoryUsageRecorder):
        return []
    prefix = f"{job.kind}:{job.id}:"
    totals: dict[tuple[str, str], JobUsage] = {}
    for event in list(recorder.events):
        if not event.operation_id.startswith(prefix):
            continue
        row = totals.setdefault(
            (event.provider, event.model),
            JobUsage(
                provider=event.provider,
                model=event.model,
                calls=0,
                errors=0,
                input_tokens=0,
                output_tokens=0,
            ),
        )
        row.calls += 1
        row.errors += event.status == "error"
        row.input_tokens += event.usage.input_tokens
        row.output_tokens += event.usage.output_tokens
    return list(totals.values())


JobNames = tuple[str, str | None, int | None]  # subject name, document title, its chapter


def _with_names(query):
    return (
        query.join(Subject, Subject.id == AiJob.subject_id)
        .outerjoin(DocumentVersion, DocumentVersion.id == AiJob.document_version_id)
        .outerjoin(Document, Document.id == DocumentVersion.document_id)
    )


def list_jobs(
    session: Session, subject_id: int | None, limit: int, *, live: bool = False
) -> list[tuple[AiJob, JobNames]]:
    """Newest first, names in the same statement; `live`: queued/running jobs still alive."""
    query = _with_names(select(AiJob, Subject.name, Document.title, Document.chapter_id))
    query = query.order_by(AiJob.id.desc()).limit(limit)
    if subject_id is not None:
        query = query.where(AiJob.subject_id == subject_id)
    if live:
        query = query.where(AiJob.status.in_(("queued", "running")))
    rows = [
        (job, (name, title, chapter_id)) for job, name, title, chapter_id in session.execute(query)
    ]
    return [row for row in rows if is_live(row[0])] if live else rows


def job_names(session: Session, job: AiJob) -> JobNames:
    query = _with_names(
        select(Subject.name, Document.title, Document.chapter_id).select_from(AiJob)
    )
    name, title, chapter_id = session.execute(query.where(AiJob.id == job.id)).one()
    return name, title, chapter_id


def job_out(job: AiJob, names: JobNames) -> JobOut:
    summarizer = _summarizers.get(job.kind)
    return JobOut(
        id=job.id,
        kind=job.kind,
        status=job.status,
        steps_total=job.steps_total,
        steps_done=job.steps_done,
        error_message=job.error_message,
        summary=summarizer(job) if summarizer else {},
        subject_id=job.subject_id,
        document_version_id=job.document_version_id,
        resumable=job.status in ("failed", "partial") or is_orphaned(job),
        usage=job_usage(job),
        created_at=job.created_at,
        updated_at=job.updated_at,
        subject_name=names[0],
        document_title=names[1],
        chapter_id=load_params(job).get("chapter_id") or names[2],
    )


def prepare_resume(session: Session, job_id: int) -> AiJob:
    job = get_job(session, job_id)
    if job.status not in ("failed", "partial") and not is_orphaned(job):
        raise HTTPException(409, NOT_RESUMABLE)
    _refuse_replaced_version(session, job)
    job.status = "queued"
    session.commit()
    return job


def _refuse_replaced_version(session: Session, job: AiJob) -> None:
    """An extraction or per-book generation fixed to a version that is no longer the latest."""
    if job.document_version_id is None:
        return
    version = session.get(DocumentVersion, job.document_version_id)
    if version is None:
        return
    latest = session.scalar(
        select(func.max(DocumentVersion.version)).where(
            DocumentVersion.document_id == version.document_id
        )
    )
    if version.version != latest:
        raise HTTPException(409, VERSION_REPLACED)


def run_job(job_id: int, settings: Settings, generator: TextGenerator) -> None:
    """Background entry point; runs every step not yet in state_json, in order."""
    if job_id in _active:
        _scheduled.discard(job_id)
        return
    _active.add(job_id)
    _scheduled.discard(job_id)
    try:
        with SessionLocal(settings.effective_database_url) as session:
            _run(session, job_id, generator)
    finally:
        _active.discard(job_id)


def _run(session: Session, job_id: int, generator: TextGenerator) -> None:
    job = session.get(AiJob, job_id)
    if job is None:
        return
    handler = _handlers[job.kind]
    job.status = "running"
    job.error_message = None
    session.commit()

    skipped: list[str] = []
    for index in range(job.steps_total):
        if str(index) in load_state(job)["steps"]:
            continue
        try:
            result = handler(session, job, index, generator)
        except StepSkipped as error:
            session.rollback()
            skipped.append(str(error))
            continue
        except ProviderError as error:
            _fail(session, job_id, str(error))
            return
        except Exception:
            logger.exception("AI job %s failed at step %s", job_id, index)
            _fail(session, job_id, UNEXPECTED_ERROR)
            return
        state = load_state(job)
        state["steps"][str(index)] = result
        job.state_json = json.dumps(state, ensure_ascii=False)
        job.steps_done = len(state["steps"])
        session.commit()

    job.status = "partial" if skipped else "done"
    job.error_message = " ".join(skipped) if skipped else None
    session.commit()


def _fail(session: Session, job_id: int, message: str) -> None:
    session.rollback()
    job = session.get(AiJob, job_id)
    job.status = "failed"
    job.error_message = message
    session.commit()
