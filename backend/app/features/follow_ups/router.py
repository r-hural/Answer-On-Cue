from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends

from app.config import Settings, get_settings
from app.db.session import SessionDep
from app.features.attempts.providers import PipelineProviders, get_pipeline_providers
from app.features.follow_ups import service
from app.features.rounds.schemas import FollowUpState

router = APIRouter(prefix="/follow-ups")

SettingsDep = Annotated[Settings, Depends(get_settings)]
ProvidersDep = Annotated[PipelineProviders, Depends(get_pipeline_providers)]


@router.post("/{follow_up_id}/retry", status_code=202)
def retry(
    follow_up_id: int,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
    background: BackgroundTasks,
) -> FollowUpState:
    # retry() counts the id as scheduled before it commits, so no read sees a gap.
    state = service.retry(session, follow_up_id)
    background.add_task(service.run, follow_up_id, settings, providers)
    return state
