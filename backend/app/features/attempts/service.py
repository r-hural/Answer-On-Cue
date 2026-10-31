"""Endpoint logic: the answer's events and audio, its result, and a manual retry."""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.features.assessment.schemas import AssessmentResult
from app.features.assessment.service import no_answer_result
from app.features.attempts import pipeline
from app.features.attempts.models import AttemptProcessing
from app.features.attempts.schemas import (
    AttemptAudioOut,
    AttemptEventsIn,
    AttemptEventsOut,
    AttemptResultOut,
    AttemptRoundOut,
    Failure,
    RecordingRef,
)
from app.features.delivery.schemas import AnswerMetrics
from app.features.recording import service as recordings
from app.features.recording.models import Recording
from app.features.rounds import service as rounds
from app.features.rounds.models import Attempt, Round, RoundItem
from app.features.rounds.schemas import RoundConfig

ATTEMPT_NOT_FOUND = "Attempt not found."
NO_RECORDED_ANSWER = "This attempt has no recorded answer."
EVENTS_DIFFER = "Different events were already saved for this attempt."
NO_ANSWER_YET = "This attempt has no answer yet."
CANNOT_RETRY = "This attempt cannot be retried."
MICROPHONE_MESSAGE = "The microphone stopped during the answer, so nothing was recorded."
CUT_OFF_MESSAGE = "Processing was cut off. Try again."
AUDIO_WAIT_SECONDS = 120
AUDIO_MISSING_MESSAGE = (
    "The recording of this answer never reached the app, so it was not assessed."
)

GIVEN_UP = "given_up"
GIVE_UP_FOLLOW_UP = "A follow-up answer cannot be given up."
GIVE_UP_RUNNING = "This answer is still being processed."
GIVE_UP_NOT_FAILED = "Only an answer that could not be processed can be given up."

RECORDED_OUTCOMES = ("answered", "interrupted")
WITHHELD_STATUSES = ("active", "paused")


def _attempt(session: Session, attempt_id: int) -> Attempt:
    attempt = session.get(Attempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, ATTEMPT_NOT_FOUND)
    return attempt


def round_of(session: Session, attempt_id: int) -> AttemptRoundOut:
    """The round an attempt belongs to: a browser's stored answer checks it before a replay, so a
    leftover after a database restore or reset never lands on another attempt with the same id."""
    item = session.get(RoundItem, _attempt(session, attempt_id).round_item_id)
    if item is None:
        raise HTTPException(404, ATTEMPT_NOT_FOUND)
    return AttemptRoundOut(attempt_id=attempt_id, round_id=item.round_id)


def _has_recorded_answer(attempt: Attempt, row: AttemptProcessing | None) -> bool:
    # A pipeline failure turns the outcome into technical_failure; its answer is still there.
    return attempt.outcome in RECORDED_OUTCOMES or (
        attempt.outcome == "technical_failure" and row is not None
    )


def _ensure_row(session: Session, attempt: Attempt) -> AttemptProcessing:
    row = pipeline.get_row(session, attempt.id)
    if row is not None:
        return row
    if attempt.outcome == "interrupted":
        row = AttemptProcessing(attempt_id=attempt.id, status="ready", stage="not_assessed")
    else:
        stage = "saved" if attempt.recording_id is not None else "waiting_for_audio"
        row = AttemptProcessing(attempt_id=attempt.id, status="processing", stage=stage)
    session.add(row)
    try:
        session.commit()
    except IntegrityError:  # a parallel request created it first
        session.rollback()
        row = pipeline.get_row(session, attempt.id)
    return row


def save_events(session: Session, attempt_id: int, body: AttemptEventsIn) -> AttemptEventsOut:
    attempt = _attempt(session, attempt_id)
    row = pipeline.get_row(session, attempt_id)
    if not _has_recorded_answer(attempt, row):
        raise HTTPException(409, NO_RECORDED_ANSWER)
    row = row or _ensure_row(session, attempt)
    if row.events_json is None:
        row.events_json = body.model_dump_json()
        session.commit()
    elif AttemptEventsIn.model_validate_json(row.events_json) != body:
        raise HTTPException(409, EVENTS_DIFFER)
    _reopen_missing_audio(session, attempt, row)
    return AttemptEventsOut(attempt_id=attempt_id, stored=True)


def _audio_out(session: Session, attempt: Attempt) -> AttemptAudioOut:
    recording = session.get(Recording, attempt.recording_id)
    return AttemptAudioOut(
        attempt_id=attempt.id, recording_id=recording.id, duration_ms=recording.duration_ms
    )


def save_audio(
    session: Session, settings: Settings, attempt_id: int, data: bytes
) -> tuple[AttemptAudioOut, bool]:
    """Stores and links the answer's WAV before any provider is involved; (out, created)."""
    attempt = _attempt(session, attempt_id)
    if not _has_recorded_answer(attempt, pipeline.get_row(session, attempt_id)):
        raise HTTPException(409, NO_RECORDED_ANSWER)
    if attempt.recording_id is not None:
        return _audio_out(session, attempt), False
    created = recordings.create_recording(session, settings, "answer", data)
    # Claim the link atomically: of two racing uploads exactly one wins, the other is discarded.
    claimed = session.execute(
        update(Attempt)
        .where(Attempt.id == attempt_id, Attempt.recording_id.is_(None))
        .values(recording_id=created.id)
        .execution_options(synchronize_session=False)
    ).rowcount
    session.commit()
    session.refresh(attempt)
    if not claimed:
        recordings.delete_recording(session, settings, created.id)
        return _audio_out(session, attempt), False
    rounds.attach_recording(session, attempt_id, created.id)  # already linked: checks only
    row = _ensure_row(session, attempt)
    if row.stage == "waiting_for_audio":
        row.stage = "saved"
        session.commit()
    _reopen_missing_audio(session, attempt, row)
    return _audio_out(session, attempt), True


def _reopen_missing_audio(session: Session, attempt: Attempt, row: AttemptProcessing) -> None:
    """Late audio after the missing-audio mark: no call was ever made, so the answer goes back
    to processing by itself once its recording and events are both stored (one conditional
    UPDATE; the router then starts the run)."""
    if attempt.recording_id is None or row.events_json is None:
        return
    reopened = session.execute(
        update(AttemptProcessing)
        .where(
            AttemptProcessing.id == row.id,
            AttemptProcessing.status == "failed",
            AttemptProcessing.failure_kind == "audio_missing",
        )
        .values(status="processing", failure_kind=None, failure_message=None)
        .execution_options(synchronize_session=False)
    ).rowcount
    session.commit()
    if reopened:
        session.refresh(row)


def _startable(attempt: Attempt, row: AttemptProcessing | None) -> bool:
    """The start condition without liveness."""
    return (
        row is not None
        and row.status == "processing"
        and attempt.outcome in ("answered", "technical_failure")
        and attempt.recording_id is not None
        and row.events_json is not None
    )


def _retryable(attempt: Attempt, row: AttemptProcessing) -> bool:
    """A missing-audio failure can be retried only once the audio and events are stored."""
    if row.failure_kind == "audio_missing":
        return attempt.recording_id is not None and row.events_json is not None
    return True


def _orphaned(attempt: Attempt, row: AttemptProcessing | None) -> bool:
    """Processing, but no run in this process (e.g. cut off by a restart)."""
    return _startable(attempt, row) and not pipeline.is_live(attempt.id)


def ready_to_start(session: Session, attempt_id: int) -> bool:
    """An upload (also one the browser replays) starts the run, but never one cut off inside a
    paid call: that waits for the learner's "Try again"."""
    from app.features.attempts.recovery import uncertain_operation  # recovery imports this module

    attempt = _attempt(session, attempt_id)
    session.refresh(attempt)
    row = pipeline.get_row(session, attempt_id)
    return _orphaned(attempt, row) and uncertain_operation(row) is None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def audio_overdue(attempt: Attempt, now: datetime | None = None) -> bool:
    """An answer still waiting for its audio AUDIO_WAIT_SECONDS after stop."""
    if (
        attempt.outcome != "answered"
        or attempt.recording_id is not None
        or attempt.processing_status != "processing"
        or attempt.ended_at is None
    ):
        return False
    now = now or datetime.now(UTC)
    return (now - _utc(attempt.ended_at)).total_seconds() > AUDIO_WAIT_SECONDS


def settle_missing_audio(session: Session, attempt: Attempt, now: datetime | None = None) -> bool:
    """Marks an answer whose audio has not arrived AUDIO_WAIT_SECONDS after stop as failed.

    Only marks (once); a late upload is still stored and a retry then processes it. The mark
    is one conditional UPDATE, so an upload that claimed the recording in between wins.
    """
    if not audio_overdue(attempt, now):
        return False
    _ensure_row(session, attempt)
    still_waiting = (
        select(Attempt.id)
        .where(
            Attempt.id == attempt.id,
            Attempt.recording_id.is_(None),
            Attempt.processing_status == "processing",
        )
        .exists()
    )
    marked = session.execute(
        update(AttemptProcessing)
        .where(
            AttemptProcessing.attempt_id == attempt.id,
            AttemptProcessing.status == "processing",
            still_waiting,
        )
        .values(
            status="failed", failure_kind="audio_missing", failure_message=AUDIO_MISSING_MESSAGE
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    session.commit()
    if not marked:
        session.refresh(attempt)
        return False
    rounds.mark_attempt_result(session, attempt.id, "failed")
    session.refresh(attempt)
    return True


def compose_result(
    attempt: Attempt,
    row: AttemptProcessing | None,
    *,
    question_version_id: int,
    config: RoundConfig,
    withheld: bool,
    recording: Recording | None = None,
    overdue: bool = False,
) -> AttemptResultOut:
    """The result from rows already read; writes nothing. overdue shows a still unsettled
    missing-audio answer as the failure it is about to become."""
    out = AttemptResultOut(
        attempt_id=attempt.id,
        outcome=attempt.outcome,
        status="processing",
        stage="waiting_for_audio",
        recording=RecordingRef(id=recording.id, duration_ms=recording.duration_ms)
        if recording is not None
        else None,
        failure=None,
        withheld=withheld,
        transcript=None,
        metrics=None,
        assessment=None,
    )

    if attempt.outcome in ("dont_know", "skipped"):
        out.status, out.stage = "ready", "done"
        if not withheld:
            out.assessment = no_answer_result(
                question_version_id, **pipeline.bounds_and_weights(config)
            )
        return out
    if attempt.outcome in (GIVEN_UP, "interrupted"):
        # Given up: final and never scored, whatever the row still holds.
        out.status, out.stage = "ready", "not_assessed"
        return out
    if overdue and (row is None or row.status == "processing"):
        out.stage = row.stage if row is not None else "waiting_for_audio"
        out.status = "failed"
        out.failure = Failure(kind="audio_missing", message=AUDIO_MISSING_MESSAGE, retryable=False)
        return out
    if row is None:
        if attempt.outcome == "technical_failure":  # the client's mic_failure
            out.status, out.stage = "failed", "not_assessed"
            out.failure = Failure(kind="microphone", message=MICROPHONE_MESSAGE, retryable=False)
        return out

    out.stage = row.stage
    if row.status == "failed":
        out.status = "failed"
        out.failure = Failure(
            kind=row.failure_kind,
            message=row.failure_message or "",
            retryable=_retryable(attempt, row),
        )
    elif row.status == "processing" and _orphaned(attempt, row):
        out.status = "failed"
        out.failure = Failure(kind="interrupted", message=CUT_OFF_MESSAGE, retryable=True)
    elif row.status == "ready":
        out.status = "ready"
        if not withheld:
            out.transcript = row.transcript_text
            out.metrics = (
                AnswerMetrics.model_validate_json(row.metrics_json) if row.metrics_json else None
            )
            out.assessment = (
                AssessmentResult.model_validate_json(row.assessment_json)
                if row.assessment_json
                else None
            )
    return out


def get_result(session: Session, attempt_id: int) -> AttemptResultOut:
    attempt = _attempt(session, attempt_id)
    session.refresh(attempt)
    if attempt.outcome == "pending":
        raise HTTPException(409, NO_ANSWER_YET)
    settle_missing_audio(session, attempt)
    ctx = rounds.get_attempt_context(session, attempt_id)
    round_row = session.get(Round, ctx.round_id)
    session.refresh(round_row)
    withheld = (
        ctx.config.feedback_timing == "exam" and round_row.status in WITHHELD_STATUSES
    ) or rounds.disclosure_pending(session, attempt)
    row = pipeline.get_row(session, attempt_id)
    if row is not None:
        session.refresh(row)
    recording = session.get(Recording, attempt.recording_id) if attempt.recording_id else None
    return compose_result(
        attempt,
        row,
        question_version_id=ctx.question_version_id,
        config=ctx.config,
        withheld=withheld,
        recording=recording,
    )


def prepare_retry(session: Session, attempt_id: int) -> None:
    """Only a failed run or one cut off; the stored audio and outputs stay."""
    attempt = _attempt(session, attempt_id)
    session.refresh(attempt)
    row = pipeline.get_row(session, attempt_id)
    if row is None or not (row.status == "failed" or _orphaned(attempt, row)):
        raise HTTPException(409, CANNOT_RETRY)
    if row.status == "failed" and not _retryable(attempt, row):
        raise HTTPException(409, CANNOT_RETRY)
    row.status = "processing"
    row.failure_kind = None
    row.failure_message = None
    session.commit()


def _settle_if_open(session: Session, attempt: Attempt) -> None:
    """An open round waiting in processing for this attempt moves on, as when a result arrives.
    Never called with an uncommitted write pending: settle_round expires the session and commits."""
    item = session.get(RoundItem, attempt.round_item_id)
    round_row = session.get(Round, item.round_id)
    session.refresh(round_row)
    if round_row.status in rounds.OPEN_STATUSES:
        rounds.settle_round(session, round_row.id, attempt.id)


def give_up(session: Session, attempt_id: int) -> AttemptResultOut:
    """Gives up a main answer that could not be processed, during or after the round: it is then
    not assessed, left out of every score, and final (no retry, no late audio, no recovery). An
    open round waiting for it moves on; a repeated give-up repairs a move that was lost."""
    attempt = _attempt(session, attempt_id)
    session.refresh(attempt)
    if attempt.outcome == GIVEN_UP:
        _settle_if_open(session, attempt)
        return get_result(session, attempt_id)
    item = session.get(RoundItem, attempt.round_item_id)
    if item.role != "main":
        raise HTTPException(409, GIVE_UP_FOLLOW_UP)
    if pipeline.is_live(attempt_id):
        raise HTTPException(409, GIVE_UP_RUNNING)
    if attempt.outcome == "pending":
        raise HTTPException(409, GIVE_UP_NOT_FAILED)
    seen = get_result(session, attempt_id)  # settles an overdue missing-audio answer first
    if seen.status == "processing":
        raise HTTPException(409, GIVE_UP_RUNNING)
    if seen.status != "failed":
        raise HTTPException(409, GIVE_UP_NOT_FAILED)
    # One transaction: the row first (a retried run that got there in between wins), then the
    # attempt; either refusal rolls both back.
    row = pipeline.get_row(session, attempt_id)
    if row is not None:
        changed = session.execute(
            update(AttemptProcessing)
            .where(
                AttemptProcessing.id == row.id,
                AttemptProcessing.status.in_(("failed", "processing")),
            )
            .values(status="ready", stage="not_assessed")
            .execution_options(synchronize_session=False)
        ).rowcount
        if not changed:
            session.rollback()
            raise HTTPException(409, GIVE_UP_RUNNING)
    if not rounds.give_up_attempt(session, attempt_id):
        session.rollback()
        raise HTTPException(409, GIVE_UP_NOT_FAILED)
    session.commit()
    session.expire_all()
    _settle_if_open(session, attempt)
    return get_result(session, attempt_id)
