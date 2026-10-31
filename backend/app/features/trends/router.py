from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.config import Settings, get_settings
from app.db.session import SessionDep
from app.features.trends import service
from app.features.trends.schemas import Series, TrendFilters, TrendsOut

router = APIRouter(prefix="/trends")

SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("")
def get_trends(
    session: SessionDep,
    settings: SettingsDep,
    subject_id: int,
    source_policy: Literal["materials", "expanded", "all"] = "materials",
    series: Series = "main",
    style: Literal["short", "open"] | None = None,
    difficulty: Annotated[Literal["recall", "apply", "analysis"] | None, Query()] = None,
    include_interrupted: bool = False,
    include_assisted: bool = False,
) -> TrendsOut:
    filters = TrendFilters(
        subject_id=subject_id,
        source_policy=source_policy,
        series=series,
        style=style,
        difficulty=difficulty,
        include_interrupted=include_interrupted,
        include_assisted=include_assisted,
    )
    return service.trends(session, settings, filters)
