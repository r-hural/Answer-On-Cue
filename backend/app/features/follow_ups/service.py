"""Follow-ups: one personalised question after a usable main answer.

The row is created ("generating") before the main result is marked, so the round waits for it;
the generation runs in the pipeline's background thread. Reads never write: a generation that is
not live in this process, or older than FOLLOW_UP_WAIT_SECONDS, is shown as failed.
"""

import json
import logging
import re
import threading
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.session import SessionLocal
from app.features.assessment.schemas import AssessmentResult
from app.features.attempts.models import AttemptProcessing
from app.features.attempts.providers import PipelineProviders
from app.features.follow_ups import prompts
from app.features.follow_ups.models import FollowUp
from app.features.follow_ups.prompts import GeneratedFollowUp
from app.features.languages.service import profile_for_subject
from app.features.library.models import DocumentVersion, SourcePassage
from app.features.providers import limits
from app.features.providers.types import Message, ProviderError
from app.features.questions import service as questions
from app.features.questions import validation
from app.features.questions.models import QuestionFamily, QuestionVersion, RubricVersion
from app.features.questions.prompts import GeneratedQuestion
from app.features.questions.schemas import QuestionWithRubric
from app.features.rounds import service as rounds
from app.features.rounds.models import Attempt, Round, RoundItem
from app.features.rounds.schemas import FollowUpFailure, FollowUpState, RoundConfig
from app.features.topics.models import TopicPassage

logger = logging.getLogger(__name__)

FOLLOW_UP_TASK = "follow_ups.generate"
FOLLOW_UP_WAIT_SECONDS = 150  # one provider call has a 120 s timeout
MAX_OUTPUT_TOKENS = 4000
MAX_PASSAGES = 12
PASSAGE_CHAR_LIMIT = 12000
STATUS = "followup"  # QuestionVersion.status: no bank view, selection or topic count lists it

NOT_FOUND = "Follow-up not found."
CANNOT_RETRY = "This follow-up cannot be retried."
CUT_OFF_MESSAGE = "Preparing the follow-up question was cut off."
TIMEOUT_MESSAGE = "Preparing the follow-up question took too long."
INVALID_MESSAGE = "The follow-up question failed: the model returned an invalid result."
UNEXPECTED_MESSAGE = "Something went wrong while preparing the follow-up question."
REASON_ANCHOR = "anchor_quote must be the learner's exact words from the transcript"
REASON_SOURCE_ISSUES = "the sources do not support a clear follow-up: ask about something else"

# Follow-ups this process is generating or has scheduled; anything else "generating" was cut off.
_active: set[int] = set()
_scheduled: set[int] = set()
_lock = threading.Lock()
_SPACES = re.compile(r"\s+")


def now() -> datetime:
    """Server clock (UTC); tests monkeypatch it."""
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    # SQLite hands timestamps back naive; they were written as UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def operation_id(follow_up_id: int, try_no: int) -> str:
    return f"follow-up-{follow_up_id}:generate:{try_no}"


# ---- liveness and the read-only view ----


def is_live(follow_up_id: int) -> bool:
    with _lock:
        return follow_up_id in _active or follow_up_id in _scheduled


def _schedule(follow_up_id: int) -> None:
    with _lock:
        _scheduled.add(follow_up_id)


def unschedule(follow_up_id: int) -> None:
    """For a request that failed after scheduling: its background task will never run."""
    with _lock:
        _scheduled.discard(follow_up_id)


def overdue(row: FollowUp, at: datetime | None = None) -> bool:
    return ((at or now()) - _utc(row.started_at)).total_seconds() > FOLLOW_UP_WAIT_SECONDS


def effective(row: FollowUp, at: datetime | None = None) -> FollowUpState:
    """The row as the round sees it; writes nothing."""
    status, failure = row.status, None
    if row.failure_kind is not None:
        failure = FollowUpFailure(kind=row.failure_kind, message=row.failure_message or "")
    if status == "generating":
        if not is_live(row.id):
            status, failure = "failed", FollowUpFailure(kind="interrupted", message=CUT_OFF_MESSAGE)
        elif overdue(row, at):
            status, failure = "failed", FollowUpFailure(kind="timeout", message=TIMEOUT_MESSAGE)
    return FollowUpState(id=row.id, status=status, failure=failure, retryable=status == "failed")


# ---- eligibility ----


def _get(session: Session, follow_up_id: int) -> FollowUp:
    row = session.get(FollowUp, follow_up_id)
    if row is None:
        raise HTTPException(404, NOT_FOUND)
    return row


def _main_result(session: Session, attempt: Attempt) -> tuple[AttemptProcessing, AssessmentResult]:
    row = session.scalar(
        select(AttemptProcessing).where(AttemptProcessing.attempt_id == attempt.id)
    )
    return row, AssessmentResult.model_validate_json(row.assessment_json)


def _usable(session: Session, attempt: Attempt) -> bool:
    """Decision 2: an answered, assessed, non-blank original main answer."""
    if attempt.attempt_kind != "initial" or attempt.outcome != "answered":
        return False
    row = session.scalar(
        select(AttemptProcessing).where(AttemptProcessing.attempt_id == attempt.id)
    )
    if row is None or row.status != "ready" or row.assessment_json is None:
        return False
    if not (row.transcript_text or "").strip():
        return False
    return AssessmentResult.model_validate_json(row.assessment_json).status == "assessed"


def _asking_now(session: Session, row: Round, item: RoundItem) -> bool:
    """The round is still on this main item, waiting in processing."""
    if row.status not in rounds.OPEN_STATUSES or row.part != "main":
        return False
    phase = row.paused_from_phase if row.status == "paused" else row.phase
    if phase != "processing":
        return False
    items = rounds._items(session, row.id)
    return row.current_index < len(items) and items[row.current_index].id == item.id


def prepare(session: Session, main_attempt_id: int) -> FollowUp | None:
    """For an eligible answer: the row ("generating"), counted live before it is committed."""
    attempt = session.get(Attempt, main_attempt_id)
    item = session.get(RoundItem, attempt.round_item_id)
    if item.role != "main":
        return None
    row = session.get(Round, item.round_id)
    session.refresh(row)
    config = RoundConfig.model_validate_json(row.config_json)
    if not config.follow_ups.enabled or not _usable(session, attempt):
        return None
    if not _asking_now(session, row, item):
        return None
    existing = session.scalar(select(FollowUp).where(FollowUp.main_item_id == item.id))
    if existing is not None:
        return existing
    follow_up = FollowUp(
        round_id=row.id,
        main_item_id=item.id,
        main_attempt_id=attempt.id,
        status="generating",
        tries=0,
        started_at=now(),
    )
    session.add(follow_up)
    try:
        session.flush()
        _schedule(follow_up.id)  # no "interrupted" gap before the thread starts
        session.commit()
    except IntegrityError:  # a parallel run created it first
        session.rollback()
        if follow_up.id is not None:
            unschedule(follow_up.id)
        return session.scalar(select(FollowUp).where(FollowUp.main_item_id == item.id))
    return follow_up


# ---- generation ----


def _passages(
    session: Session, question: QuestionWithRubric, config: RoundConfig
) -> list[tuple[int, str]]:
    """The cited passages first, then the primary topic's; non-excluded, in scope, capped."""
    cited = list(
        dict.fromkeys(
            [i for p in question.rubric.essential_points for i in p.passage_ids]
            + [i for p in question.rubric.enrichment_points for i in p.passage_ids]
        )
    )
    topic_ids: list[int] = []
    if question.primary_topic_id is not None:
        topic_ids = list(
            session.scalars(
                select(TopicPassage.passage_id)
                .join(SourcePassage, SourcePassage.id == TopicPassage.passage_id)
                .where(TopicPassage.topic_id == question.primary_topic_id)
                .order_by(SourcePassage.document_version_id, SourcePassage.ordinal)
            )
        )
    order = list(dict.fromkeys(cited + topic_ids))
    query = (
        select(SourcePassage, DocumentVersion.document_id)
        .join(DocumentVersion, DocumentVersion.id == SourcePassage.document_version_id)
        .where(SourcePassage.id.in_(order), SourcePassage.excluded.is_(False))
    )
    if config.scope.kind == "documents":
        query = query.where(DocumentVersion.document_id.in_(config.scope.document_ids))
    found = {passage.id: passage for passage, _ in session.execute(query)}
    chosen: list[tuple[int, str]] = []
    used = 0
    for pid in order:
        passage = found.get(pid)
        if passage is None or used + len(passage.text) > PASSAGE_CHAR_LIMIT:
            continue
        chosen.append((passage.id, passage.text))
        used += len(passage.text)
        if len(chosen) >= MAX_PASSAGES:
            break
    return chosen


def _normal(text: str) -> str:
    return _SPACES.sub(" ", text.casefold()).strip()


def _check(
    generated: GeneratedFollowUp,
    *,
    main: QuestionWithRubric,
    transcript: str,
    allowed: set[int],
    config: RoundConfig,
) -> tuple[GeneratedFollowUp | None, str | None]:
    """Code checks (decision 4): the question rules plus the anchor rule; a cleaned copy."""
    anchor = (generated.anchor_quote or "").strip()
    if anchor and _normal(anchor) not in _normal(transcript):
        if generated.target in prompts.ANCHORED_TARGETS:
            return None, REASON_ANCHOR
        anchor = ""
    if generated.target in prompts.ANCHORED_TARGETS and not anchor:
        return None, REASON_ANCHOR
    wrapper = GeneratedQuestion(
        text=generated.text,
        style="short",
        difficulty=main.difficulty,
        rubric=generated.rubric,
        suggested_prep_s=config.follow_ups.prep_seconds,
        suggested_answer_s=config.follow_ups.answer_seconds,
    )
    checked = validation.validate_question(wrapper, allowed, [main.text], policy=main.source_policy)
    if checked.question is None:
        return None, checked.reason
    if checked.question.rubric.source_issues:  # never ask what the sources leave unclear
        return None, REASON_SOURCE_ISSUES
    return (
        GeneratedFollowUp(
            target=generated.target,
            anchor_quote=anchor or None,
            text=checked.question.text,
            rubric=checked.question.rubric,
        ),
        None,
    )


def _ask_model(
    generator,
    row: FollowUp,
    messages: list[Message],
    check,
    *,
    system: str,
) -> tuple[GeneratedFollowUp, object, int]:
    """One call and at most one retry on invalid output; (follow-up, provenance, calls)."""
    base = operation_id(row.id, row.tries)
    reason = "invalid output"
    calls = 0
    for op_id in limits.attempt_ids(base):
        if calls and overdue(row):  # the learner was told it timed out: no second call
            raise _Invalid(reason)
        calls += 1
        try:
            response = generator.generate_structured(
                FOLLOW_UP_TASK,
                system,
                messages,
                GeneratedFollowUp,
                operation_id=op_id,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            )
        except ProviderError as error:
            if error.kind != "invalid_output":
                raise
            reason = f"output did not match the schema: {error.message}"
            continue
        cleaned, reason = check(response.value)
        if cleaned is not None:
            return cleaned, response.provenance, calls
        messages = [
            *messages,
            Message(role="assistant", content=response.value.model_dump_json()),
            Message(role="user", content=prompts.retry_message(reason)),
        ]
    raise _Invalid(reason)


class _Invalid(Exception):
    pass


def _save(
    session: Session,
    row: FollowUp,
    main_item: RoundItem,
    main: QuestionWithRubric,
    generated: GeneratedFollowUp,
    provenance: dict,
    config: RoundConfig,
) -> bool:
    """Question, rubric, item and pending attempt, then ready — in one transaction
    (saved before it is asked). False when the row is no longer generating (continued, late)."""
    main_version = session.get(QuestionVersion, main.version_id)
    family = QuestionFamily(
        subject_id=session.get(QuestionFamily, main_version.family_id).subject_id,
        primary_topic_id=main.primary_topic_id,
    )
    session.add(family)
    session.flush()
    version = QuestionVersion(
        family_id=family.id,
        version=1,
        text=generated.text,
        style="short",
        difficulty=main.difficulty,
        source_policy=main.source_policy,
        objective_id=main.objective_id,
        secondary_topic_ids="[]",
        suggested_prep_s=config.follow_ups.prep_seconds,
        suggested_answer_s=config.follow_ups.answer_seconds,
        status=STATUS,
        provenance_json=json.dumps(provenance, ensure_ascii=False),
    )
    session.add(version)
    session.flush()
    rubric = RubricVersion(
        question_version_id=version.id, rubric_json=generated.rubric.model_dump_json()
    )
    session.add(rubric)
    session.flush()
    item = RoundItem(
        round_id=main_item.round_id,
        ordinal=main_item.ordinal,
        role="followup",
        parent_item_id=main_item.id,
        question_version_id=version.id,
        rubric_version_id=rubric.id,
    )
    session.add(item)
    session.flush()
    session.add(
        Attempt(
            round_item_id=item.id,
            attempt_kind="followup",
            parent_attempt_id=row.main_attempt_id,
            outcome="pending",
            started_at=now(),
        )
    )
    marked = session.execute(
        update(FollowUp)
        .where(FollowUp.id == row.id, FollowUp.status == "generating")
        .values(
            status="ready",
            item_id=item.id,
            target=generated.target,
            provenance_json=json.dumps(provenance, ensure_ascii=False),
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    if not marked:
        session.rollback()
        return False
    session.commit()
    return True


def _fail(session: Session, follow_up_id: int, kind: str, message: str) -> None:
    """Only a generation still running is marked; continued or saved ones stay as they are."""
    session.rollback()
    session.execute(
        update(FollowUp)
        .where(FollowUp.id == follow_up_id, FollowUp.status == "generating")
        .values(status="failed", failure_kind=kind, failure_message=message)
        .execution_options(synchronize_session=False)
    )
    session.commit()


def _generate(
    session: Session, settings: Settings, follow_up_id: int, providers: PipelineProviders
) -> None:
    row = session.get(FollowUp, follow_up_id)
    if row is None:
        return
    session.refresh(row)
    if row.status != "generating":
        return
    if row.tries == 0:  # the first try; a retry has already counted its own
        row.tries = 1
    session.commit()  # nothing stays open during the provider call

    main_attempt = session.get(Attempt, row.main_attempt_id)
    main_item = session.get(RoundItem, row.main_item_id)
    round_row = session.get(Round, row.round_id)
    config = RoundConfig.model_validate_json(round_row.config_json)
    profile = profile_for_subject(session, round_row.subject_id)
    main = questions.get_question(session, main_item.question_version_id, with_rubric=True)
    processing, assessment = _main_result(session, main_attempt)
    transcript = processing.transcript_text or ""
    passages = _passages(session, main, config)
    texts = {p.id: p.text for p in main.rubric.essential_points}
    points = [
        (texts.get(p.point_id, p.point_id), p.status, p.note)
        for p in (assessment.ai.points if assessment.ai else [])
    ]
    mistakes = [
        (m.quote, m.correction) for m in (assessment.ai.factual_mistakes if assessment.ai else [])
    ]
    message = prompts.user_message(
        main.text, main.rubric, passages, transcript, points, mistakes, profile=profile
    )
    allowed = {pid for pid, _ in passages}

    def check(generated: GeneratedFollowUp):
        return _check(generated, main=main, transcript=transcript, allowed=allowed, config=config)

    try:
        generated, provenance, calls = _ask_model(
            providers.follow_up_generator(),
            row,
            [Message(role="user", content=message)],
            check,
            system=prompts.follow_up_system(main.source_policy, profile=profile),
        )
    except ProviderError as error:
        _fail(session, row.id, error.kind, error.message)
        return
    except _Invalid:
        if overdue(row):
            _fail(session, row.id, "timeout", TIMEOUT_MESSAGE)
        else:
            _fail(session, row.id, "invalid_output", INVALID_MESSAGE)
        return

    session.refresh(row)
    if overdue(row):  # the learner was told it timed out: never ask it now
        _fail(session, row.id, "timeout", TIMEOUT_MESSAGE)
        return
    saved = _save(
        session,
        row,
        main_item,
        main,
        generated,
        {
            "origin": "ai",
            "prompt_version": prompts.FOLLOW_UP_PROMPT_VERSION,
            "main_attempt_id": row.main_attempt_id,
            "anchor_quote": generated.anchor_quote,
            "passage_ids": sorted(allowed),
            "calls": calls,
            **provenance.model_dump(),
        },
        config,
    )
    if saved:
        rounds.settle_round(session, row.round_id)


def generate(
    session: Session, settings: Settings, follow_up_id: int, providers: PipelineProviders
) -> None:
    """One try; never raises (a failure is stored on the row, the round never waits for ever)."""
    with _lock:
        _scheduled.discard(follow_up_id)
        if follow_up_id in _active:
            return
        _active.add(follow_up_id)
    try:
        _generate(session, settings, follow_up_id, providers)
    except Exception:
        logger.exception("Follow-up %s failed while generating", follow_up_id)
        try:
            _fail(session, follow_up_id, "unexpected", UNEXPECTED_MESSAGE)
        except Exception:
            logger.exception("Follow-up %s could not be marked failed", follow_up_id)
    finally:
        with _lock:
            _active.discard(follow_up_id)


def generate_pending(
    session: Session, settings: Settings, main_attempt_id: int, providers: PipelineProviders
) -> None:
    """The pipeline's hook after a run: the first try only, so a rerun never generates twice."""
    row = session.scalar(select(FollowUp).where(FollowUp.main_attempt_id == main_attempt_id))
    if row is None:
        return
    session.refresh(row)
    if row.status != "generating" or row.tries != 0:
        return
    generate(session, settings, row.id, providers)


def run(follow_up_id: int, settings: Settings, providers: PipelineProviders) -> None:
    """Background entry point for a retry."""
    with SessionLocal(settings.effective_database_url) as session:
        generate(session, settings, follow_up_id, providers)


# ---- retry ----


def retry(session: Session, follow_up_id: int) -> FollowUpState:
    """A failed (or cut-off, or overdue) generation of the item the round is waiting on.

    One conditional UPDATE: a new try with a new wait window. The caller schedules
    the run; the id is counted live before the commit."""
    row = _get(session, follow_up_id)
    session.refresh(row)
    round_row = session.get(Round, row.round_id)
    session.refresh(round_row)
    main_item = session.get(RoundItem, row.main_item_id)
    seen = effective(row)
    if seen.status != "failed" or not _asking_now(session, round_row, main_item):
        raise HTTPException(409, CANNOT_RETRY)
    _schedule(row.id)
    changed = session.execute(
        update(FollowUp)
        .where(FollowUp.id == row.id, FollowUp.status == row.status, FollowUp.tries == row.tries)
        .values(
            status="generating",
            tries=row.tries + 1,
            started_at=now(),
            failure_kind=None,
            failure_message=None,
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    if not changed:
        session.rollback()
        unschedule(row.id)
        raise HTTPException(409, CANNOT_RETRY)
    session.commit()
    session.refresh(row)
    return effective(row)
