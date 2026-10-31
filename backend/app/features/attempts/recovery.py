"""Recovery after a reload or a restart: what a cut-off answer needs, and the hand-off.

Recovery never creates an attempt and never repeats a paid call that may already have run: a
run cut off during its provider call is "uncertain" and waits for the learner's "Try again"
(a new call with a new operation id). Every other cut-off resumes by itself, because each stage
skips what is already stored.
"""

from typing import Literal

from fastapi import BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.features.attempts import pipeline
from app.features.attempts.models import AttemptProcessing
from app.features.attempts.providers import PipelineProviders
from app.features.attempts.service import ATTEMPT_NOT_FOUND, NO_ANSWER_YET, _startable
from app.features.follow_ups.models import FollowUp
from app.features.rounds import service as rounds
from app.features.rounds.models import Attempt, Round, RoundItem
from app.features.rounds.service import ROUND_NOT_FOUND

RecoveryAction = Literal[
    "none", "running", "completed", "resumed", "confirm", "waiting_for_audio", "failed"
]

RECOVERABLE_OUTCOMES = ("answered", "technical_failure")


class RecoveryOut(BaseModel):
    attempt_id: int
    action: RecoveryAction
    operation_id: str | None = None  # the call that may already have run (action "confirm")


class RoundRecoveryOut(BaseModel):
    round_id: int
    attempts: list[RecoveryOut]


def uncertain_operation(row: AttemptProcessing) -> str | None:
    """The current try's call when the run stopped inside it: it may have reached the provider."""
    if row.stage == "transcribing" and row.transcript_text is None:
        return pipeline.operation_id(row.attempt_id, "transcribe", row.transcribe_tries)
    if row.stage == "assessing" and row.assessment_json is None:
        return pipeline.operation_id(row.attempt_id, "assess", row.assess_tries)
    return None


def classify(attempt: Attempt, row: AttemptProcessing | None, live: bool) -> RecoveryAction:
    """What recovery does for an answer; pure."""
    if attempt.outcome not in RECOVERABLE_OUTCOMES:
        return "none"
    if row is None:
        if attempt.outcome == "answered" and attempt.processing_status == "processing":
            return "waiting_for_audio"
        return "failed"  # the client's microphone failure: nothing to process
    if live:
        return "running"
    if row.status == "processing":
        if not _startable(attempt, row):
            return "waiting_for_audio"
        return "confirm" if uncertain_operation(row) is not None else "resumed"
    if attempt.processing_status in ("processing", "failed") and attempt.processing_status != (
        row.status
    ):
        return "completed"  # the verdict is stored but never reached the round
    return "failed" if row.status == "failed" else "none"


def _follow_up_waiting(session: Session, attempt_id: int) -> bool:
    """A follow-up whose first generation never started (tries == 0): no call has been made."""
    row = session.scalar(select(FollowUp).where(FollowUp.main_attempt_id == attempt_id))
    if row is None:
        return False
    session.refresh(row)
    return row.status == "generating" and row.tries == 0


def _settle_missed(session: Session, attempt: Attempt, row: AttemptProcessing | None) -> bool:
    """A verdict that reached the attempt while the run stopped before the round moved on (between
    the two commits of `mark_attempt_result`): settles the round, or starts the follow-up's first
    try that never ran. Idempotent; makes no call itself."""
    if row is None or row.status not in ("ready", "failed"):
        return False
    if attempt.processing_status != row.status:
        return False
    item = session.get(RoundItem, attempt.round_item_id)
    moved = item is not None and rounds.settle_round(session, item.round_id, attempt.id)
    return moved or _follow_up_waiting(session, attempt.id)


def recover(
    session: Session,
    attempt_id: int,
    background: BackgroundTasks,
    settings: Settings,
    providers: PipelineProviders,
) -> RecoveryOut:
    attempt = session.get(Attempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, ATTEMPT_NOT_FOUND)
    session.refresh(attempt)
    if attempt.outcome == "pending":
        raise HTTPException(409, NO_ANSWER_YET)
    return _recover(session, attempt, background, settings, providers)


def _recover(
    session: Session,
    attempt: Attempt,
    background: BackgroundTasks,
    settings: Settings,
    providers: PipelineProviders,
) -> RecoveryOut:
    row = pipeline.get_row(session, attempt.id)
    if row is not None:
        session.refresh(row)
    action = classify(attempt, row, pipeline.is_live(attempt.id))
    operation = uncertain_operation(row) if action == "confirm" else None
    if action == "resumed":
        pipeline.schedule(background, attempt.id, settings, providers)
    elif action == "none" and _settle_missed(session, attempt, row):
        action = "completed"
        if _follow_up_waiting(session, attempt.id):
            pipeline.schedule(background, attempt.id, settings, providers)
    elif action == "completed":
        pipeline.hand_off(session, attempt.id, row.status)
        if _follow_up_waiting(session, attempt.id):
            # run() finds nothing left to process and goes on to the follow-up's first try
            pipeline.schedule(background, attempt.id, settings, providers)
    return RecoveryOut(attempt_id=attempt.id, action=action, operation_id=operation)


def recover_round(
    session: Session,
    round_id: int,
    background: BackgroundTasks,
    settings: Settings,
    providers: PipelineProviders,
) -> RoundRecoveryOut:
    """Every recorded answer of the round, in item order."""
    if session.get(Round, round_id) is None:
        raise HTTPException(404, ROUND_NOT_FOUND)
    attempts = session.scalars(
        select(Attempt)
        .join(RoundItem, RoundItem.id == Attempt.round_item_id)
        .where(RoundItem.round_id == round_id, Attempt.outcome.in_(RECOVERABLE_OUTCOMES))
        .order_by(RoundItem.ordinal, RoundItem.id, Attempt.id)
    ).all()
    return RoundRecoveryOut(
        round_id=round_id,
        attempts=[_recover(session, a, background, settings, providers) for a in attempts],
    )
