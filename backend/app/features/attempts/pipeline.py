"""The attempt pipeline: VAD → transcription → delivery metrics → assessment, stage by stage.

Each stage is skipped when its output is already stored and is committed as soon as it succeeds,
so a retry repeats only the stage that failed and no billable call runs twice. A try counter is
bumped and committed before each provider call, so every call has its own operation id.
"""

import contextlib
import json
import logging
import threading
from typing import Literal

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.session import SessionLocal
from app.features.assessment.schemas import (
    DEFAULT_BAND_BOUNDS,
    DEFAULT_FACTUAL_WEIGHTS,
    AssessmentError,
    AssessmentInput,
)
from app.features.assessment.service import assess, has_speech
from app.features.attempts.models import AttemptProcessing
from app.features.attempts.providers import PipelineProviders
from app.features.attempts.schemas import AttemptEventsIn
from app.features.delivery.metrics import compute_answer_metrics
from app.features.delivery.schemas import AnswerMetrics
from app.features.follow_ups import service as follow_ups
from app.features.languages.profiles import get_profile
from app.features.languages.service import subject_language
from app.features.providers.types import ProviderError
from app.features.questions import service as questions
from app.features.recording import service as recordings
from app.features.recording.schemas import VadResult
from app.features.rounds import service as rounds
from app.features.rounds.models import Attempt
from app.features.rounds.schemas import RoundConfig
from app.features.topics import service as topics

logger = logging.getLogger(__name__)

UNEXPECTED_MESSAGE = "Something went wrong while processing. Try again."

# Attempts this process is running or has scheduled; anything else still "processing" was cut off.
_active: set[int] = set()
_scheduled: set[int] = set()
# Retries accepted while a run was still finishing; that run goes round once more.
_rerun: set[int] = set()
# Request handlers and background tasks run on threadpool threads: check-and-add under one lock.
_lock = threading.Lock()


class NoCall:
    """Stands in for the assessor where assess() decides without an AI call."""

    def generate_structured(self, *args, **kwargs):
        raise RuntimeError("No AI call is expected here.")


def operation_id(attempt_id: int, stage: Literal["transcribe", "assess"], try_no: int) -> str:
    """Unique per stage and try, so a retried call is never mistaken for a repeat."""
    return f"attempt-{attempt_id}:{stage}:{try_no}"


def bounds_and_weights(config: RoundConfig) -> dict:
    """The round's snapshot; rounds created before config snapshots existed stored none."""
    return {
        "bounds": config.band_bounds or DEFAULT_BAND_BOUNDS,
        "weights": config.factual_weights or DEFAULT_FACTUAL_WEIGHTS,
    }


def get_row(session: Session, attempt_id: int) -> AttemptProcessing | None:
    return session.scalar(
        select(AttemptProcessing).where(AttemptProcessing.attempt_id == attempt_id)
    )


def is_live(attempt_id: int) -> bool:
    return attempt_id in _active or attempt_id in _scheduled


def schedule(
    background: BackgroundTasks, attempt_id: int, settings: Settings, providers: PipelineProviders
) -> None:
    """Runs the pipeline after the response; until then this process counts it as live.

    At most one run per attempt: a second call while one is scheduled does nothing, and one while
    a run is going on makes that run go round once more (a retry accepted as it was finishing).
    """
    with _lock:
        if attempt_id in _scheduled:
            return
        if attempt_id in _active:
            _rerun.add(attempt_id)
            return
        _scheduled.add(attempt_id)
    background.add_task(run, attempt_id, settings, providers)


def unschedule(attempt_id: int) -> None:
    """For a request that failed after scheduling: its background task will never run."""
    with _lock:
        _scheduled.discard(attempt_id)


def run(attempt_id: int, settings: Settings, providers: PipelineProviders) -> None:
    """Background entry point; one run per attempt at a time."""
    with _lock:
        _scheduled.discard(attempt_id)
        if attempt_id in _active:
            return
        _active.add(attempt_id)
    try:
        while True:
            with SessionLocal(settings.effective_database_url) as session:
                _run(session, settings, attempt_id, providers)
                _follow_up(session, settings, attempt_id, providers)
            with _lock:
                if attempt_id not in _rerun:
                    break
                _rerun.discard(attempt_id)
    finally:
        with _lock:
            _active.discard(attempt_id)
            _rerun.discard(attempt_id)


def _run(
    session: Session, settings: Settings, attempt_id: int, providers: PipelineProviders
) -> None:
    row = get_row(session, attempt_id)
    if row is None or row.status != "processing":
        return
    try:
        _stages(session, settings, row, providers)
    except ProviderError as error:
        _fail(session, attempt_id, error.kind, error.message)
    except AssessmentError as error:
        _fail(session, attempt_id, "invalid_output", error.message)
    except Exception:
        logger.exception("Attempt %s failed while processing", attempt_id)
        _fail(session, attempt_id, "unexpected", UNEXPECTED_MESSAGE)


def _prepare_follow_up(session: Session, attempt_id: int) -> None:
    """Before the main result is marked, so the round waits for the follow-up; never blocks it."""
    try:
        follow_ups.prepare(session, attempt_id)
    except Exception:
        logger.exception("Attempt %s: the follow-up could not be prepared", attempt_id)
        session.rollback()


def _follow_up(
    session: Session, settings: Settings, attempt_id: int, providers: PipelineProviders
) -> None:
    try:
        follow_ups.generate_pending(session, settings, attempt_id, providers)
    except Exception:
        logger.exception("Attempt %s: the follow-up generation failed", attempt_id)


def _stage(session: Session, row: AttemptProcessing, stage: str) -> None:
    row.stage = stage
    session.commit()


def _stages(
    session: Session, settings: Settings, row: AttemptProcessing, providers: PipelineProviders
) -> None:
    attempt_id = row.attempt_id
    ctx = rounds.get_attempt_context(session, attempt_id)
    attempt = session.get(Attempt, attempt_id)
    recording = recordings.get_recording(session, attempt.recording_id)
    events = AttemptEventsIn.model_validate_json(row.events_json)

    if recording.vad_json is None:
        _stage(session, row, "analysing")
        vad = recordings.analyze_recording(session, settings, recording.id)
    else:
        vad = VadResult.model_validate_json(recording.vad_json)

    question = questions.get_question(session, ctx.question_version_id, with_rubric=True)
    language = subject_language(session, ctx.subject_id)
    # Silence or unusable audio: no transcription, the transcript stays null.
    if vad.speech_ms > 0 and row.transcript_text is None:
        topic_ids = [question.primary_topic_id] if question.primary_topic_id else None
        glossary = topics.get_glossary(session, ctx.subject_id, topic_ids)
        row.stage = "transcribing"
        row.transcribe_tries += 1
        session.commit()
        audio = recordings.audio_path(settings, recording).read_bytes()
        transcript = providers.transcriber().transcribe(
            audio,
            "audio/wav",
            glossary,
            operation_id=operation_id(attempt_id, "transcribe", row.transcribe_tries),
            language=language,
        )
        row.transcript_text = transcript.text
        row.transcript_meta_json = json.dumps(
            {
                "language": transcript.language,
                "provenance": transcript.provenance.model_dump(),
                "usage": transcript.usage.model_dump(),
                "glossary": glossary,
            },
            ensure_ascii=False,
        )
        session.commit()

    if row.metrics_json is None:
        metrics = compute_answer_metrics(
            events, vad, row.transcript_text, profile=get_profile(language)
        )
        row.metrics_json = metrics.model_dump_json()
        session.commit()
    else:
        metrics = AnswerMetrics.model_validate_json(row.metrics_json)

    if row.assessment_json is None:
        inp = AssessmentInput(
            question=question,
            transcript=row.transcript_text,
            outcome="answered",
            attempt_kind=ctx.attempt_kind,
            interrupted=False,
            delivery=metrics,
        )
        if has_speech(inp):
            row.stage = "assessing"
            row.assess_tries += 1
            session.commit()
            generator = providers.assessor()
        else:
            generator = NoCall()  # assess() returns no_speech without a call (and a key)
        result = assess(
            inp,
            generator,
            operation_id=operation_id(attempt_id, "assess", row.assess_tries),
            profile=get_profile(language),
            **bounds_and_weights(ctx.config),
        )
        row.assessment_json = result.model_dump_json()
        session.commit()

    row.status = "ready"
    row.stage = "done"
    session.commit()
    _prepare_follow_up(session, attempt_id)
    rounds.mark_attempt_result(session, attempt_id, "ready")


def hand_off(session: Session, attempt_id: int, status: Literal["ready", "failed"]) -> None:
    """Moves the round on for a stored verdict that never reached it (a run stopped between its
    two commits). Makes no call; the follow-up is only prepared, as at the end of a run."""
    if status == "ready":
        _prepare_follow_up(session, attempt_id)
    with contextlib.suppress(HTTPException):
        rounds.mark_attempt_result(session, attempt_id, status)


def _fail(session: Session, attempt_id: int, kind: str, message: str) -> None:
    """Keeps every stored output; the round attempt becomes a technical failure, never a score."""
    session.rollback()
    row = get_row(session, attempt_id)
    if row is None:  # the round was deleted meanwhile
        return
    row.status = "failed"
    row.failure_kind = kind
    row.failure_message = message
    session.commit()
    with contextlib.suppress(HTTPException):
        rounds.mark_attempt_result(session, attempt_id, "failed")
