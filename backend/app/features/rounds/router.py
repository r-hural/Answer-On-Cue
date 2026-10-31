from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Response

from app.config import Settings, get_settings
from app.db.session import SessionDep
from app.features.attempts import recovery
from app.features.attempts.providers import PipelineProviders, get_pipeline_providers
from app.features.jobs.schemas import JobStarted
from app.features.providers.registry import GenerationDep
from app.features.rounds import extension, retry, service
from app.features.rounds.schemas import (
    ActionIn,
    ExtensionOffer,
    RetryOffer,
    RoundCreate,
    RoundListItem,
    RoundState,
    RoundSummary,
)

# Exam screens use these: no response carries a rubric, model answer or passage text.
router = APIRouter(prefix="/rounds")

SettingsDep = Annotated[Settings, Depends(get_settings)]
ProvidersDep = Annotated[PipelineProviders, Depends(get_pipeline_providers)]


@router.post("", status_code=201)
def create_round(body: RoundCreate, session: SessionDep, settings: SettingsDep) -> RoundState:
    state = service.create_round(session, settings, body)
    # Lazy: the backup feature imports attempts, follow-ups and jobs.
    from app.features.backup import cleanup

    cleanup.prune_if_due(settings)
    return state


@router.get("")
def list_rounds(subject_id: int, session: SessionDep) -> list[RoundListItem]:
    return service.list_rounds(session, subject_id)


@router.get("/{round_id}")
def get_round(round_id: int, session: SessionDep) -> RoundState:
    return service.get_state(session, round_id)


@router.post("/{round_id}/actions")
def apply_action(round_id: int, body: ActionIn, session: SessionDep) -> RoundState:
    return service.apply_action(session, round_id, body)


@router.post("/{round_id}/recover")
def recover_round(
    round_id: int,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
    background: BackgroundTasks,
) -> recovery.RoundRecoveryOut:
    """Every recorded answer of the round: certain cut-offs resume, uncertain ones wait."""
    return recovery.recover_round(session, round_id, background, settings, providers)


@router.get("/{round_id}/summary")
def round_summary(round_id: int, session: SessionDep) -> RoundSummary:
    return service.round_summary(session, round_id)


@router.get("/{round_id}/extension")
def get_extension(round_id: int, session: SessionDep) -> ExtensionOffer:
    return extension.offer(session, round_id)


@router.post("/{round_id}/extension", status_code=201)
def start_extension(
    round_id: int, session: SessionDep, settings: SettingsDep, response: Response
) -> RoundState:
    state, created = extension.start(session, settings, round_id)
    if not created:
        response.status_code = 200
    return state


@router.post("/{round_id}/extension/generate", status_code=202)
def generate_extension_questions(
    round_id: int,
    session: SessionDep,
    settings: SettingsDep,
    generator: GenerationDep,
    background: BackgroundTasks,
) -> JobStarted:
    return extension.start_generation(session, settings, round_id, generator, background)


@router.get("/{round_id}/retry")
def get_retry(round_id: int, session: SessionDep) -> RetryOffer:
    return retry.offer(session, round_id)


@router.post("/{round_id}/retry", status_code=201)
def start_retry(
    round_id: int, session: SessionDep, settings: SettingsDep, response: Response
) -> RoundState:
    state, created = retry.start(session, round_id, settings)
    if not created:
        response.status_code = 200
    return state
