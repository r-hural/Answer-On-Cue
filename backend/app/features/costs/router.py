from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.config import Settings, get_settings
from app.db.session import SessionDep
from app.features.costs import estimates, usage_report
from app.features.costs.prices import PRICES, PRICES_CHECKED
from app.features.costs.schemas import (
    Group,
    JobEstimate,
    MonthSummary,
    PriceOut,
    RoundEstimate,
    UsageTable,
)
from app.features.questions.schemas import GenerateIn

router = APIRouter(prefix="/costs", tags=["costs"])

SettingsDep = Annotated[Settings, Depends(get_settings)]
Month = Annotated[str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]


@router.get("/summary")
def summary(session: SessionDep, settings: SettingsDep, month: Month = None) -> MonthSummary:
    return usage_report.summary(session, settings, month)


@router.get("/usage")
def usage(session: SessionDep, month: Month = None, group: Group = "operation") -> UsageTable:
    return usage_report.usage_table(session, month, group)


@router.get("/estimate/round")
def estimate_round(
    session: SessionDep,
    settings: SettingsDep,
    subject_id: int,
    question_count: Annotated[int, Query(ge=1, le=30)],
    answer_seconds: Annotated[int, Query(ge=5, le=600)],
    follow_ups: bool = False,
) -> RoundEstimate:
    return estimates.round_estimate(
        session,
        settings,
        subject_id=subject_id,
        question_count=question_count,
        answer_seconds=answer_seconds,
        follow_ups=follow_ups,
    )


@router.post("/estimate/generation")
def estimate_generation(
    body: GenerateIn, session: SessionDep, settings: SettingsDep
) -> JobEstimate:
    """Read only, like the GETs: the job the Generate button would start, priced."""
    return estimates.generation_estimate(session, settings, body)


@router.get("/estimate/extraction")
def estimate_extraction(
    document_version_id: int, session: SessionDep, settings: SettingsDep
) -> JobEstimate:
    return estimates.extraction_estimate(session, settings, document_version_id)


def _role(price) -> str:
    if price.kind:
        return price.kind
    if price.audio_per_min:
        return "transcription"
    if price.chars_per_m:
        return "voice"
    return "text"


@router.get("/prices")
def prices() -> list[PriceOut]:
    return [
        PriceOut(
            provider=provider,
            model=model,
            role=_role(price),
            input_per_m=price.input_per_m,
            output_per_m=price.output_per_m,
            audio_per_min=price.audio_per_min,
            chars_per_m=price.chars_per_m,
            checked=PRICES_CHECKED,
        )
        for (provider, model), price in PRICES.items()
    ]
