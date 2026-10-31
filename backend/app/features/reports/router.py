from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.db.session import SessionDep
from app.features.reports import service
from app.features.reports.schemas import (
    AttemptReport,
    HistoryRow,
    RoundReport,
    TranscriptReportIn,
    TranscriptReportOut,
)

router = APIRouter(prefix="/reports")


@router.get("/attempts/{attempt_id}")
def get_attempt_report(attempt_id: int, session: SessionDep) -> AttemptReport:
    return service.attempt_report(session, attempt_id)


@router.put("/attempts/{attempt_id}/transcript-report")
def put_transcript_report(
    attempt_id: int, body: TranscriptReportIn, session: SessionDep
) -> TranscriptReportOut:
    return service.put_transcript_report(session, attempt_id, body)


@router.get("/rounds/{round_id}")
def get_round_report(round_id: int, session: SessionDep) -> RoundReport:
    return service.round_report(session, round_id)


@router.get("/history")
def get_history(
    session: SessionDep,
    subject_id: Annotated[int | None, Query()] = None,
    feedback_timing: Annotated[Literal["practice", "exam"] | None, Query()] = None,
    source_policy: Annotated[Literal["materials", "expanded", "all"] | None, Query()] = None,
    chapter_id: Annotated[int | None, Query()] = None,
) -> list[HistoryRow]:
    return service.history(session, subject_id, feedback_timing, source_policy, chapter_id)
