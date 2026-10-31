"""Rounds: creation with a config snapshot, the phase flow, pause events, pipeline hooks."""

import random
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.config import Settings
from app.features.attempts.models import AttemptProcessing
from app.features.costs import estimates
from app.features.costs.spending import ROUND_OVER_BUDGET, money
from app.features.coverage import service as coverage
from app.features.follow_ups.models import FollowUp
from app.features.languages.profiles import get_profile
from app.features.languages.service import subject_language
from app.features.library import service as library
from app.features.library.models import Chapter, Document, DocumentVersion, SourcePassage
from app.features.questions import service as questions
from app.features.questions.models import QuestionFamily, QuestionVersion, RubricVersion
from app.features.questions.schemas import Rubric
from app.features.recording.models import Recording
from app.features.rounds.adaptive import lines_from_coverage, select_adaptive
from app.features.rounds.models import Attempt, Round, RoundEvent, RoundItem
from app.features.rounds.schemas import (
    ActionIn,
    AttemptContext,
    AttemptOut,
    CurrentQuestion,
    FollowUpState,
    ItemOut,
    PhaseClock,
    RoundConfig,
    RoundCreate,
    RoundListItem,
    RoundState,
    RoundSummary,
)
from app.features.rounds.selection import Candidate, InsufficientQuestions, select_standard
from app.features.rounds.state import (
    NOT_NOW,
    MachineState,
    Transition,
    TransitionError,
    settle,
    transition,
)
from app.features.topics.models import Topic
from app.features.voice.per_language import (
    VOICE_WRONG_LANGUAGE,
    default_voice,
    voice_for_language,
    voice_matches_language,
)

__all__ = ["InsufficientQuestions"]

ROUND_NOT_FOUND = "Round not found."
ATTEMPT_NOT_FOUND = "Attempt not found."
RECORDING_NOT_FOUND = "Recording not found."
FOREIGN_SCOPE = "The selected topics or documents do not belong to this subject."
NEED_PREMIUM_VOICE = "Choose a premium voice in Settings."
TOO_FEW = "Not enough questions for the selected settings: {available} available."

RECORDING_NOT_ALLOWED = "This recording cannot be attached to this attempt."
RESULT_NOT_ALLOWED = "This attempt is not waiting for a result."

OPEN_STATUSES = ("active", "paused")
# An interrupted attempt keeps its partial audio; pending covers an upload racing the stop.
RECORDABLE_OUTCOMES = ("pending", "answered", "interrupted")
UNRESOLVED_OUTCOMES = ("technical_failure",)
NO_ANSWER_OUTCOMES = ("dont_know", "skipped")  # attempted without an answer: a repeat may follow
SETTLE_TRIES = 3


def now() -> datetime:
    """Server clock (UTC); tests monkeypatch it."""
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    # SQLite hands timestamps back naive; they were written as UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# ---- creation ----


def _check_scope(session: Session, subject_id: int, config: RoundConfig) -> None:
    scope = config.scope
    if scope.topic_ids:
        found = session.scalar(
            select(func.count())
            .select_from(Topic)
            .where(Topic.id.in_(scope.topic_ids), Topic.subject_id == subject_id)
        )
        if found != len(set(scope.topic_ids)):
            raise HTTPException(422, FOREIGN_SCOPE)
    if scope.document_ids:
        found = session.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.id.in_(scope.document_ids), Document.subject_id == subject_id)
        )
        if found != len(set(scope.document_ids)):
            raise HTTPException(422, FOREIGN_SCOPE)
    if scope.chapter_id is not None:
        found = session.scalar(
            select(func.count())
            .select_from(Chapter)
            .where(Chapter.id == scope.chapter_id, Chapter.subject_id == subject_id)
        )
        if not found:
            raise HTTPException(422, FOREIGN_SCOPE)


def _snapshot(
    session: Session, settings: Settings, subject_id: int, config: RoundConfig
) -> RoundConfig:
    """The subject's language and the voice that reads the round, fixed at creation.

    A premium round always stores its voice: the override's, else the language's."""
    language = subject_language(session, subject_id)
    profile = get_profile(language)
    audio = config.question_audio
    if audio.mode == "premium":
        if audio.provider is not None:
            provider = audio.provider
            voice = audio.voice or default_voice(settings, provider, profile)
            if not voice_matches_language(provider, voice, profile):
                raise HTTPException(422, VOICE_WRONG_LANGUAGE)
        else:
            # An override voice without a provider is ignored, as before.
            provider, voice = voice_for_language(session, settings, language)
            if provider == "browser":
                raise HTTPException(422, NEED_PREMIUM_VOICE)
        audio = audio.model_copy(
            update={
                "provider": provider,
                "voice": voice,
                # One speaking rate for every language.
                "rate": audio.rate if audio.rate is not None else settings.voice_rate,
            }
        )
    else:
        audio = audio.model_copy(update={"provider": None, "voice": None, "rate": None})
    return config.model_copy(
        update={
            "band_bounds": tuple(settings.factual_band_bounds),
            "factual_weights": tuple(settings.factual_weights),
            "question_audio": audio,
            "language": language,  # the client's value is ignored
        }
    )


def _last_asked(session: Session, subject_id: int) -> dict[int, int]:
    """family_id → how many rounds ago it was last asked (1 = the previous non-abandoned round)."""
    rounds = list(
        session.scalars(
            select(Round.id)
            .where(Round.subject_id == subject_id, Round.status != "abandoned")
            .order_by(Round.started_at.desc(), Round.id.desc())
        )
    )
    rank = {round_id: n for n, round_id in enumerate(rounds, start=1)}
    asked: dict[int, int] = {}
    if not rounds:
        return asked
    rows = session.execute(
        select(RoundItem.round_id, QuestionFamily.id)
        .join(QuestionVersion, QuestionVersion.id == RoundItem.question_version_id)
        .join(QuestionFamily, QuestionFamily.id == QuestionVersion.family_id)
        .where(RoundItem.round_id.in_(rounds))
    )
    for round_id, family_id in rows:
        asked[family_id] = min(asked.get(family_id, rank[round_id]), rank[round_id])
    return asked


def _candidates(session: Session, subject_id: int) -> tuple[list[Candidate], dict[int, int]]:
    """Active questions of the subject, plus question_version_id → rubric_version_id."""
    bank = questions.list_questions(session, subject_id)
    rubrics = {
        r.question_version_id: r
        for r in session.scalars(
            select(RubricVersion).where(
                RubricVersion.question_version_id.in_([q.version_id for q in bank])
            )
        )
    }
    cited = {
        version_id: {
            i
            for p in Rubric.model_validate_json(r.rubric_json).essential_points
            for i in p.passage_ids
        }
        for version_id, r in rubrics.items()
    }
    all_passages = set().union(*cited.values()) if cited else set()
    documents = dict(
        session.execute(
            select(SourcePassage.id, DocumentVersion.document_id)
            .join(DocumentVersion, DocumentVersion.id == SourcePassage.document_version_id)
            .where(SourcePassage.id.in_(all_passages))
        ).all()
    )
    asked = _last_asked(session, subject_id)
    chapter_of = dict(
        session.execute(
            select(Topic.id, Topic.chapter_id).where(Topic.subject_id == subject_id)
        ).all()
    )
    candidates = [
        Candidate(
            question_version_id=q.version_id,
            family_id=q.family_id,
            primary_topic_id=q.primary_topic_id,
            style=q.style,
            difficulty=q.difficulty,
            document_ids=sorted({documents[i] for i in cited[q.version_id] if i in documents}),
            last_asked_round=asked.get(q.family_id),
            objective_id=q.objective_id,
            source_policy=q.source_policy,
            chapter_id=chapter_of.get(q.primary_topic_id),
        )
        for q in bank
        if q.version_id in rubrics
    ]
    return candidates, {v: r.id for v, r in rubrics.items()}


def _insert_round(
    session: Session,
    subject_id: int,
    config: RoundConfig,
    chosen: list[int],
    rubric_ids: dict[int, int],
    seed: int,
    *,
    parent_round_id: int | None = None,
    selections: list[str | None] | None = None,
    parent_item_ids: list[int] | None = None,
) -> Round:
    """The round, its main items and the first pending attempt; the caller commits. A retry
    round's items link to the original items (`parent_item_ids`)."""
    row = Round(
        subject_id=subject_id,
        round_type=config.round_type,
        status="active",
        phase="question",
        current_index=0,
        config_json=config.model_dump_json(),
        seed=seed,
        started_at=now(),
        parent_round_id=parent_round_id,
    )
    session.add(row)
    session.flush()
    items = [
        RoundItem(
            round_id=row.id,
            ordinal=n,
            role="main",
            question_version_id=version_id,
            rubric_version_id=rubric_ids[version_id],
            selection=selections[n] if selections is not None else None,
            parent_item_id=parent_item_ids[n] if parent_item_ids is not None else None,
        )
        for n, version_id in enumerate(chosen)
    ]
    session.add_all(items)
    session.flush()
    session.add(_new_attempt(items[0].id, kind=series_kind(config.round_type)))
    return row


def _too_few(error: InsufficientQuestions) -> HTTPException:
    return HTTPException(
        409, {"message": TOO_FEW.format(available=error.available), "available": error.available}
    )


def check_budget(
    session: Session, settings: Settings, subject_id: int, config: RoundConfig, count: int
) -> None:
    """Only with a ceiling: a round whose high estimate passes what is left cannot start, so it
    is never cut off halfway (409 with the message and the estimate)."""
    if not settings.spending_ceiling_usd:
        return
    audio = config.question_audio
    estimate = estimates.round_estimate(
        session,
        settings,
        subject_id=subject_id,
        question_count=count,
        answer_seconds=config.answer_seconds,
        follow_ups=config.follow_ups.enabled,
        follow_up_seconds=config.follow_ups.answer_seconds,
        # The snapshot always names a premium round's provider.
        voice_provider=audio.provider if audio.mode == "premium" else "browser",
    )
    if estimate.fits:
        return
    message = ROUND_OVER_BUDGET.format(
        low=money(estimate.low_usd),
        high=money(estimate.high_usd).removeprefix("$"),  # "$0.45–0.55", as the setup shows it
        left=money(estimate.month.remaining_usd or 0.0),
        ceiling=money(estimate.month.ceiling_usd),
    )
    raise HTTPException(409, {"message": message, "estimate": estimate.model_dump(mode="json")})


def create_round(session: Session, settings: Settings, data: RoundCreate) -> RoundState:
    library.get_subject(session, data.subject_id)
    _check_scope(session, data.subject_id, data.config)
    config = _snapshot(session, settings, data.subject_id, data.config)
    candidates, rubric_ids = _candidates(session, data.subject_id)
    seed = random.randrange(2**31)
    selections: list[str | None] | None = None
    try:
        if config.round_type == "adaptive":
            scope = config.scope
            chapter_id = scope.chapter_id if scope.kind == "chapter" else None
            lines = lines_from_coverage(
                coverage.subject_coverage(
                    session, data.subject_id, config.source_policy, chapter_id=chapter_id
                )
            )
            picked = select_adaptive(candidates, lines, config, seed)
            chosen = [version_id for version_id, _ in picked]
            selections = [selection for _, selection in picked]
        else:
            chosen = select_standard(candidates, config, seed)
    except InsufficientQuestions as error:
        raise _too_few(error) from error
    check_budget(session, settings, data.subject_id, config, len(chosen))
    row = _insert_round(
        session, data.subject_id, config, chosen, rubric_ids, seed, selections=selections
    )
    session.commit()
    return get_state(session, row.id)


def _new_attempt(item_id: int, parent_id: int | None = None, kind: str = "initial") -> Attempt:
    return Attempt(
        round_item_id=item_id,
        attempt_kind=kind,
        parent_attempt_id=parent_id,
        outcome="pending",
        started_at=now(),
    )


# ---- reading ----


def _get_round(session: Session, round_id: int) -> Round:
    row = session.get(Round, round_id)
    if row is None:
        raise HTTPException(404, ROUND_NOT_FOUND)
    return row


def _config(row: Round) -> RoundConfig:
    return RoundConfig.model_validate_json(row.config_json)


def _items(session: Session, round_id: int) -> list[RoundItem]:
    return list(
        session.scalars(
            select(RoundItem)
            .where(RoundItem.round_id == round_id, RoundItem.role == "main")
            .order_by(RoundItem.ordinal)
        )
    )


def _attempts(session: Session, items: list[RoundItem]) -> dict[int, list[Attempt]]:
    by_item: dict[int, list[Attempt]] = {item.id: [] for item in items}
    for attempt in session.scalars(
        select(Attempt).where(Attempt.round_item_id.in_(by_item)).order_by(Attempt.id)
    ):
        by_item[attempt.round_item_id].append(attempt)
    return by_item


def _events(session: Session, round_id: int) -> list[RoundEvent]:
    return list(
        session.scalars(
            select(RoundEvent).where(RoundEvent.round_id == round_id).order_by(RoundEvent.id)
        )
    )


def _pauses(row: Round, events: list[RoundEvent]) -> tuple[int, int]:
    """Pause count and paused milliseconds; an open pause runs until abandon or now."""
    count, paused_ms, opened = 0, 0, None
    for event in events:
        if event.action == "pause":
            count += 1
            opened = _utc(event.at)
        elif event.action in ("resume", "abandon") and opened is not None:
            paused_ms += int((_utc(event.at) - opened).total_seconds() * 1000)
            opened = None
    if opened is not None and row.status == "paused":
        paused_ms += int((now() - opened).total_seconds() * 1000)
    return count, paused_ms


def _clock(
    row: Round, config: RoundConfig, attempt: Attempt | None, events: list[RoundEvent]
) -> PhaseClock | None:
    """For a timed phase or a pause only (elsewhere a repeated read stays identical), from rows
    get_state has already read: no statement of its own."""
    timed = row.status == "active" and row.phase in ("preparation", "recording")
    if not timed and row.status != "paused":
        return None
    clock = PhaseClock(
        server_now=now(),
        phase_started_at=None,
        prep_deadline_at=None,
        answer_deadline_at=None,
        paused_at=None,
    )
    if row.status == "paused":
        pause = next((e for e in reversed(events) if e.action == "pause"), None)
        clock.paused_at = _utc(pause.at) if pause is not None else None
        return clock
    if attempt is None:
        return clock
    timers = config.follow_ups if row.part == "followup" else config
    if row.phase == "preparation" and attempt.started_at is not None:
        clock.phase_started_at = _utc(attempt.started_at)
        clock.prep_deadline_at = clock.phase_started_at + timedelta(seconds=timers.prep_seconds)
    elif row.phase == "recording":
        start = next(
            (
                e
                for e in reversed(events)
                if e.action == "start_answer" and e.attempt_id == attempt.id
            ),
            None,
        )
        if start is not None:
            clock.phase_started_at = _utc(start.at)
            clock.answer_deadline_at = clock.phase_started_at + timedelta(
                seconds=timers.answer_seconds
            )
    return clock


def _attempt_out(attempt: Attempt | None) -> AttemptOut | None:
    if attempt is None:
        return None
    return AttemptOut(
        id=attempt.id,
        attempt_kind=attempt.attempt_kind,
        outcome=attempt.outcome,
        processing_status=attempt.processing_status,
        parent_attempt_id=attempt.parent_attempt_id,
        start_reason=attempt.start_reason,
        recording_id=attempt.recording_id,
    )


def _item_status(row: Round, index: int, latest: Attempt | None) -> str:
    if row.status == "finished" or index < row.current_index:
        return "done"
    if index == row.current_index:
        if row.status in OPEN_STATUSES:
            return "current"
        if latest is not None and latest.outcome not in ("pending", "interrupted"):
            return "done"
    return "pending"


def _follow_up_row(session: Session, main_item_id: int) -> FollowUp | None:
    return session.scalar(select(FollowUp).where(FollowUp.main_item_id == main_item_id))


def _follow_up_state(row: FollowUp | None) -> FollowUpState | None:
    from app.features.follow_ups import service as follow_ups  # follow_ups imports this module

    return follow_ups.effective(row) if row is not None else None


def _latest(session: Session, item_id: int) -> Attempt | None:
    return session.scalar(
        select(Attempt).where(Attempt.round_item_id == item_id).order_by(Attempt.id.desc()).limit(1)
    )


def get_state(session: Session, round_id: int) -> RoundState:
    row = _get_round(session, round_id)
    items = _items(session, round_id)
    attempts = _attempts(session, items)
    open_round = row.status in OPEN_STATUSES
    current = current_attempt = follow_up = latest = None
    config = _config(row)
    can_answer_again = False
    if open_round and row.current_index < len(items):
        item = items[row.current_index]
        follow_row = _follow_up_row(session, item.id)
        follow_up = _follow_up_state(follow_row)
        asked, latest = item, attempts[item.id][-1] if attempts[item.id] else None
        if row.part == "followup" and follow_row is not None and follow_row.item_id is not None:
            asked = session.get(RoundItem, follow_row.item_id)
            latest = _latest(session, asked.id)
        question = questions.get_question(session, asked.question_version_id, with_rubric=False)
        current = CurrentQuestion(
            item_id=asked.id,
            question_version_id=asked.question_version_id,
            text=question.text,
            style=question.style,
            difficulty=question.difficulty,
        )
        current_attempt = _attempt_out(latest)
        can_answer_again = _repeat_allowed(config, row, attempts[item.id])
    events = _events(session, round_id)
    pause_count, paused_ms = _pauses(row, events)
    return RoundState(
        id=row.id,
        subject_id=row.subject_id,
        status=row.status,
        phase=row.phase,
        part=row.part,
        current_index=row.current_index,
        total=len(items),
        config=config,
        current=current,
        current_attempt=current_attempt,
        items=[
            ItemOut(
                id=item.id,
                ordinal=item.ordinal,
                role=item.role,
                question_version_id=item.question_version_id,
                status=_item_status(row, n, _final_attempt(attempts[item.id])),
                latest_attempt=_attempt_out(_final_attempt(attempts[item.id])),
            )
            for n, item in enumerate(items)
        ],
        pause_count=pause_count,
        paused_ms=paused_ms,
        follow_up=follow_up,
        can_answer_again=can_answer_again,
        clock=_clock(row, config, latest, events),
    )


def _repeat_allowed(config: RoundConfig, row: Round, tried: list[Attempt]) -> bool:
    """Answer again: once per main question, on its practice feedback, after a
    usable result or no answer; a technical failure's recovery is "Try again" instead."""
    if not config.immediate_repeat or config.feedback_timing != "practice":
        return False
    if (row.status, row.phase, row.part) != ("active", "feedback", "main"):
        return False
    if any(a.attempt_kind == "immediate" for a in tried):
        return False
    final = _final_attempt(tried)
    if final is None:
        return False
    if final.outcome in NO_ANSWER_OUTCOMES:
        return True
    return final.outcome == "answered" and final.processing_status == "ready"


# ---- actions ----


def _current_attempt(session: Session, row: Round) -> tuple[list[RoundItem], Attempt | None]:
    """The latest follow-up attempt in the follow-up part, else the main item's latest."""
    items = _items(session, row.id)
    if row.current_index >= len(items):
        return items, None
    item_id = items[row.current_index].id
    if row.part == "followup":
        follow_row = _follow_up_row(session, item_id)
        if follow_row is not None and follow_row.item_id is not None:
            item_id = follow_row.item_id
    return items, _latest(session, item_id)


def _machine(
    session: Session, row: Round, items: list[RoundItem], attempt: Attempt | None
) -> MachineState:
    config = _config(row)
    follow_up = None
    repeat_allowed = False
    if row.current_index < len(items):
        item_id = items[row.current_index].id
        state = _follow_up_state(_follow_up_row(session, item_id))
        follow_up = state.status if state is not None else None
        if config.immediate_repeat and row.phase == "feedback":
            tried = list(
                session.scalars(
                    select(Attempt).where(Attempt.round_item_id == item_id).order_by(Attempt.id)
                )
            )
            repeat_allowed = _repeat_allowed(config, row, tried)
    cut_off = False
    if (
        row.phase == "processing"
        and row.part == "main"
        and attempt is not None
        and attempt.processing_status == "processing"
    ):
        from app.features.attempts import pipeline  # attempts imports this module
        from app.features.attempts import service as attempts_service

        cut_off = attempts_service._orphaned(attempt, pipeline.get_row(session, attempt.id))
    return MachineState(
        status=row.status,
        phase=row.phase,
        paused_from_phase=row.paused_from_phase,
        feedback_timing=config.feedback_timing,
        is_last=row.current_index >= len(items) - 1,
        result_ready=attempt is not None and attempt.processing_status in ("ready", "failed"),
        part=row.part,
        follow_ups_on=config.follow_ups.enabled,
        follow_up=follow_up,
        repeat_allowed=repeat_allowed,
        repeating=row.part == "main"
        and attempt is not None
        and attempt.attempt_kind == "immediate",
        cut_off=cut_off,
    )


def _is_duplicate(
    row: Round, action: ActionIn, last: RoundEvent | None, current: Attempt | None
) -> bool:
    """A repeated click or retried request: the last applied action, sent again."""
    if last is None or (last.action, last.from_phase) != (action.action, action.from_phase):
        return False
    if action.attempt_id is not None:
        # Exam skip goes question → question: only the attempt shows that it already happened.
        return last.attempt_id == action.attempt_id and (
            current is None or current.id != action.attempt_id
        )
    if action.from_phase != row.phase:
        return True
    # pause and resume keep or restore the phase, so a repeat shows only in the status.
    return (action.action, row.status) in (("pause", "paused"), ("resume", "active"))


def apply_action(session: Session, round_id: int, action: ActionIn) -> RoundState:
    try:
        return _apply_action(session, round_id, action)
    except StaleDataError:
        # A parallel request for the same round committed first (e.g. a double click).
        session.rollback()
        session.expire_all()
        row = _get_round(session, round_id)
        events = _events(session, round_id)
        _, attempt = _current_attempt(session, row)
        if _is_duplicate(row, action, events[-1] if events else None, attempt):
            return get_state(session, round_id)
        raise HTTPException(409, NOT_NOW) from None


def _apply(
    session: Session,
    row: Round,
    items: list[RoundItem],
    attempt: Attempt | None,
    result: Transition,
    at: datetime,
) -> None:
    """Writes one transition (a client action or an internal move); the caller commits."""
    if attempt is not None:
        if result.outcome is not None:
            attempt.outcome = result.outcome
        if result.processing_status is not None:
            attempt.processing_status = result.processing_status
        if result.start_reason is not None:
            attempt.start_reason = result.start_reason
        if result.start_attempt:
            attempt.started_at = at
        if result.end_attempt:
            attempt.ended_at = at
    if result.new_attempt and attempt is not None:
        # A restart keeps the kind: a follow-up restarts as a follow-up, on its own item. A
        # repeat (answer_again) is a new kind on the same item, linked to the original; a
        # restarted repeat stays linked to that original.
        kind = result.attempt_kind or attempt.attempt_kind
        parent_id = attempt.id
        if kind == "immediate" and attempt.attempt_kind == "immediate":
            parent_id = attempt.parent_attempt_id
        session.add(_new_attempt(attempt.round_item_id, parent_id=parent_id, kind=kind))
    if result.advance:
        row.current_index += 1
        session.add(_new_attempt(items[row.current_index].id, kind=series_kind(row.round_type)))
    row.version += 1
    row.status = result.status
    row.phase = result.phase
    row.paused_from_phase = result.paused_from_phase
    if result.part is not None:
        row.part = result.part
    if result.status in ("finished", "abandoned"):
        row.finished_at = at


def _apply_action(session: Session, round_id: int, action: ActionIn) -> RoundState:
    row = _get_round(session, round_id)
    events = _events(session, round_id)
    items, attempt = _current_attempt(session, row)
    if _is_duplicate(row, action, events[-1] if events else None, attempt):
        return get_state(session, round_id)
    machine = _machine(session, row, items, attempt)
    try:
        if action.from_phase != row.phase and row.status in OPEN_STATUSES:
            raise TransitionError(NOT_NOW)
        if action.attempt_id is not None and (attempt is None or attempt.id != action.attempt_id):
            raise TransitionError(NOT_NOW)
        result = transition(machine, action.action, action.start_reason)
    except TransitionError as error:
        raise HTTPException(409, error.message) from error

    at = now()
    from_phase = row.phase
    main_item_id = items[row.current_index].id if row.current_index < len(items) else None
    _apply(session, row, items, attempt, result, at)
    if action.action == "no_follow_up" and main_item_id is not None:
        # Recorded, with the failure (if any) kept; a generation finishing later is discarded.
        session.execute(
            update(FollowUp)
            .where(
                FollowUp.main_item_id == main_item_id,
                FollowUp.status.in_(("generating", "failed")),
            )
            .values(status="omitted")
            .execution_options(synchronize_session=False)
        )
    session.add(
        RoundEvent(
            round_id=row.id,
            attempt_id=attempt.id if attempt is not None else None,
            action=action.action,
            from_phase=from_phase,
            to_phase=result.phase,
            status_after=result.status,
            at=at,
        )
    )
    session.commit()
    return get_state(session, round_id)


# ---- hooks for the attempt pipeline ----


def _get_attempt(session: Session, attempt_id: int) -> Attempt:
    attempt = session.get(Attempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, ATTEMPT_NOT_FOUND)
    return attempt


def get_attempt_context(session: Session, attempt_id: int) -> AttemptContext:
    attempt = _get_attempt(session, attempt_id)
    item = session.get(RoundItem, attempt.round_item_id)
    row = session.get(Round, item.round_id)
    return AttemptContext(
        attempt_id=attempt.id,
        round_id=row.id,
        item_id=item.id,
        subject_id=row.subject_id,
        question_version_id=item.question_version_id,
        rubric_version_id=item.rubric_version_id,
        attempt_kind=attempt.attempt_kind,
        interrupted=attempt.outcome == "interrupted",
        config=_config(row),
    )


def attach_recording(session: Session, attempt_id: int, recording_id: int) -> None:
    """Links the attempt's audio once; a retry with the same recording changes nothing."""
    attempt = _get_attempt(session, attempt_id)
    if session.get(Recording, recording_id) is None:
        raise HTTPException(404, RECORDING_NOT_FOUND)
    if attempt.recording_id == recording_id:
        return
    if attempt.recording_id is not None or attempt.outcome not in RECORDABLE_OUTCOMES:
        raise HTTPException(409, RECORDING_NOT_ALLOWED)
    used = session.scalar(select(Attempt.id).where(Attempt.recording_id == recording_id))
    if used is not None:
        raise HTTPException(409, RECORDING_NOT_ALLOWED)
    attempt.recording_id = recording_id
    session.commit()


def mark_attempt_result(
    session: Session, attempt_id: int, status: Literal["ready", "failed"]
) -> RoundState:
    """The pipeline's verdict on an answered attempt. failed → ready is allowed (a retried
    pipeline succeeded); ready is final."""
    attempt = _get_attempt(session, attempt_id)
    item = session.get(RoundItem, attempt.round_item_id)
    row = session.get(Round, item.round_id)
    if attempt.processing_status == status:
        return get_state(session, row.id)
    if attempt.processing_status not in ("processing", "failed") or attempt.outcome not in (
        "answered",
        "technical_failure",
    ):
        raise HTTPException(409, RESULT_NOT_ALLOWED)
    attempt.processing_status = status
    attempt.outcome = "technical_failure" if status == "failed" else "answered"
    session.commit()
    settle_round(session, row.id)
    return get_state(session, row.id)


def give_up_attempt(session: Session, attempt_id: int) -> bool:
    """The attempt half of giving up an answer, inside the caller's transaction (no commit):
    one conditional UPDATE of an answer still waiting for or without a result. True when it
    changed the row."""
    changed = session.execute(
        update(Attempt)
        .where(
            Attempt.id == attempt_id,
            Attempt.outcome.in_(("answered", "technical_failure")),
            or_(
                Attempt.processing_status.in_(("processing", "failed")),
                Attempt.processing_status.is_(None),
            ),
        )
        .values(outcome="given_up", processing_status="ready")
        .execution_options(synchronize_session=False)
    ).rowcount
    return bool(changed)


def settle_round(session: Session, round_id: int, attempt_id: int | None = None) -> bool:
    """Applies the internal move once a result or a follow-up has arrived. Writes no RoundEvent
    (client duplicate detection stays as it was) but bumps the version, so a client action read
    before it loses with 409 and resyncs. With `attempt_id`, moves only while that attempt is the
    current one. True when the round moved."""
    for _ in range(SETTLE_TRIES):
        try:
            session.expire_all()  # read what other sessions and plain UPDATEs committed
            row = _get_round(session, round_id)
            items, attempt = _current_attempt(session, row)
            if attempt_id is not None and (attempt is None or attempt.id != attempt_id):
                return False
            moved = settle(_machine(session, row, items, attempt))
            if moved is None:
                return False
            _apply(session, row, items, attempt, moved, now())
            session.commit()
            return True
        except StaleDataError:
            session.rollback()
            session.expire_all()
    return False


def disclosure_pending(session: Session, attempt: Attempt) -> bool:
    """A practice main answer whose follow-up is still to come: its feedback waits (decision 7)."""
    if attempt.attempt_kind != "initial":
        return False  # repeats and retries never get a follow-up, so nothing waits
    item = session.get(RoundItem, attempt.round_item_id)
    if item is None or item.role != "main":
        return False
    row = session.get(Round, item.round_id)
    if row.status not in OPEN_STATUSES:
        return False
    items = _items(session, row.id)
    if row.current_index >= len(items) or items[row.current_index].id != item.id:
        return False
    if row.part == "followup":
        return True
    if row.phase != "processing":
        return False
    follow_row = _follow_up_row(session, item.id)
    if follow_row is not None:
        return follow_row.status in ("generating", "failed", "ready")
    # the pipeline has stored the result but not yet decided on the follow-up (prepare) nor
    # marked the attempt (mark_attempt_result): the follow-up may still come, so wait
    if not _config(row).follow_ups.enabled or attempt.processing_status == "ready":
        return False
    stored = session.scalar(
        select(AttemptProcessing.status).where(AttemptProcessing.attempt_id == attempt.id)
    )
    return stored == "ready"


# ---- summary and listing ----


def series_kind(round_type: str) -> Literal["initial", "retry"]:
    """The attempt kind of a round's own main answers: a retry round's are retries."""
    return "retry" if round_type == "retry" else "initial"


def _final_attempt(attempts: list[Attempt]) -> Attempt | None:
    """The item's latest attempt that was not interrupted (else its latest). An immediate
    repeat (Answer again) is never an item's final attempt: it is assisted and kept apart."""
    attempts = [a for a in attempts if a.attempt_kind != "immediate"]
    kept = [a for a in attempts if a.outcome != "interrupted"]
    if kept:
        return kept[-1]
    return attempts[-1] if attempts else None


def round_summary(session: Session, round_id: int) -> RoundSummary:
    row = _get_round(session, round_id)
    items = _items(session, round_id)
    attempts = _attempts(session, items)
    outcomes: dict[str, int] = {}
    unresolved: list[int] = []
    for item in items:
        final = _final_attempt(attempts[item.id])
        outcome = final.outcome if final is not None else "pending"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        if final is not None and (
            final.processing_status == "processing" or final.outcome in UNRESOLVED_OUTCOMES
        ):
            unresolved.append(final.id)
    interrupted = [
        a.id
        for item in items
        for a in attempts[item.id]
        if a.outcome == "interrupted" and a.attempt_kind != "immediate"
    ]
    events = _events(session, round_id)
    pause_count, paused_ms = _pauses(row, events)
    return RoundSummary(
        round_id=row.id,
        status=row.status,
        provisional=bool(unresolved) or row.status == "abandoned",
        outcomes=outcomes,
        unresolved_attempt_ids=unresolved,
        interrupted_attempt_ids=interrupted,
        pause_count=pause_count,
        paused_ms=paused_ms,
    )


def list_rounds(session: Session, subject_id: int, limit: int = 20) -> list[RoundListItem]:
    rows = list(
        session.scalars(
            select(Round)
            .where(
                Round.subject_id == subject_id,
                # A closed extension lives on its round's summary; an open one can be resumed.
                (Round.round_type != "extension") | Round.status.in_(OPEN_STATUSES),
            )
            .order_by(Round.started_at.desc(), Round.id.desc())
            .limit(limit)
        )
    )
    listed = []
    for row in rows:
        items = _items(session, row.id)
        attempts = _attempts(session, items)
        done = 0
        for item in items:
            final = _final_attempt(attempts[item.id])
            if final is not None and final.outcome not in ("pending", "interrupted"):
                done += 1
        listed.append(
            RoundListItem(
                id=row.id,
                status=row.status,
                started_at=_utc(row.started_at),
                finished_at=_utc(row.finished_at) if row.finished_at else None,
                total=len(items),
                done=done,
                feedback_timing=_config(row).feedback_timing,
                round_type=row.round_type,
            )
        )
    return listed
