from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Response, UploadFile

from app.config import Settings, get_settings
from app.db.session import SessionDep
from app.features.attempts import pipeline, recovery, service
from app.features.attempts.providers import PipelineProviders, get_pipeline_providers
from app.features.attempts.schemas import (
    AttemptAudioOut,
    AttemptEventsIn,
    AttemptEventsOut,
    AttemptResultOut,
    AttemptRoundOut,
)
from app.features.recording import service as recordings

# The exam screen uses these: the result is withheld while an exam round is still running.
router = APIRouter(prefix="/attempts")

SettingsDep = Annotated[Settings, Depends(get_settings)]
ProvidersDep = Annotated[PipelineProviders, Depends(get_pipeline_providers)]


def _start_if_ready(
    session, attempt_id: int, settings: Settings, providers, background: BackgroundTasks
) -> None:
    if service.ready_to_start(session, attempt_id):
        pipeline.schedule(background, attempt_id, settings, providers)


@router.post("/{attempt_id}/events")
def post_events(
    attempt_id: int,
    body: AttemptEventsIn,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
    background: BackgroundTasks,
) -> AttemptEventsOut:
    out = service.save_events(session, attempt_id, body)
    _start_if_ready(session, attempt_id, settings, providers, background)
    return out


@router.post("/{attempt_id}/audio", status_code=201)
def post_audio(
    attempt_id: int,
    file: Annotated[UploadFile, File()],
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
    background: BackgroundTasks,
) -> AttemptAudioOut:
    out, created = service.save_audio(
        session, settings, attempt_id, recordings.read_upload(file.file)
    )
    if not created:
        response.status_code = 200
    _start_if_ready(session, attempt_id, settings, providers, background)
    return out


@router.get("/{attempt_id}/result")
def get_result(attempt_id: int, session: SessionDep) -> AttemptResultOut:
    return service.get_result(session, attempt_id)


@router.get("/{attempt_id}/round")
def get_round_of(attempt_id: int, session: SessionDep) -> AttemptRoundOut:
    return service.round_of(session, attempt_id)


@router.post("/{attempt_id}/retry", status_code=202)
def retry(
    attempt_id: int,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
    background: BackgroundTasks,
) -> AttemptResultOut:
    service.prepare_retry(session, attempt_id)
    # prepare_retry has checked the attempt; schedule() itself keeps it to one run at a time.
    pipeline.schedule(background, attempt_id, settings, providers)
    try:
        return service.get_result(session, attempt_id)
    except Exception:
        pipeline.unschedule(attempt_id)
        raise


@router.post("/{attempt_id}/give-up")
def give_up(attempt_id: int, session: SessionDep) -> AttemptResultOut:
    """Leaves an answer that could not be processed out of the score, as not assessed, during
    or after the round."""
    return service.give_up(session, attempt_id)


@router.post("/{attempt_id}/recover")
def recover(
    attempt_id: int,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
    background: BackgroundTasks,
) -> recovery.RecoveryOut:
    """After a reload or a restart; never repeats a paid call that may already have run."""
    out = recovery.recover(session, attempt_id, background, settings, providers)
    if out.action == "resumed":
        response.status_code = 202
    return out
