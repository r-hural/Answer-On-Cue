"""End-of-round adaptive extension: N new questions on the round's weak topics.

The extension is a linked round (round_type "extension", parent_round_id): it reuses the whole
phase machine, and its answers are first independent attempts at new questions, so coverage
counts them while the parent's primary score never sees them.
"""

import json
import random
from dataclasses import dataclass, field

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.features.attempts.models import AttemptProcessing
from app.features.attempts.service import audio_overdue
from app.features.jobs import service as jobs
from app.features.jobs.schemas import JobStarted
from app.features.providers.base import TextGenerator
from app.features.questions import service as questions
from app.features.questions.models import QuestionFamily, QuestionVersion
from app.features.rounds import service as rounds
from app.features.rounds.adaptive import WeakTarget, select_extension
from app.features.rounds.models import Attempt, Round, RoundItem
from app.features.rounds.schemas import (
    ExtensionOffer,
    ExtensionStatus,
    FollowUpConfig,
    RoundConfig,
    RoundState,
    WeakTopic,
)
from app.features.topics.models import LearningObjective, Topic

EXTENSION_NOT_NOW = "The adaptive extension is not available for this round now."
LOW_BANDS = ("weak", "needs_improvement")
NO_EVIDENCE_OUTCOMES = ("dont_know", "skipped")  # attempted, so weak


@dataclass
class _Reading:
    """Everything one offer read finds; start and generate act on the same reading."""

    row: Round
    config: RoundConfig
    status: ExtensionStatus
    child: Round | None = None
    targets: list[WeakTarget] = field(default_factory=list)
    topics: list[WeakTopic] = field(default_factory=list)
    families: set[int] = field(default_factory=set)
    available: int = 0
    job_id: int | None = None
    needs_review: int = 0


def _child(session: Session, round_id: int) -> Round | None:
    return session.scalar(
        select(Round).where(Round.parent_round_id == round_id, Round.round_type == "extension")
    )


def _main_answers(session: Session, row: Round) -> tuple[bool, list[WeakTarget], set[int]]:
    """(waiting, weak targets, families asked) from three statements, whatever the round size."""
    items = session.execute(
        select(
            RoundItem,
            QuestionVersion.objective_id,
            QuestionVersion.family_id,
            QuestionFamily.primary_topic_id,
            LearningObjective.topic_id,
        )
        .join(QuestionVersion, QuestionVersion.id == RoundItem.question_version_id)
        .join(QuestionFamily, QuestionFamily.id == QuestionVersion.family_id)
        .outerjoin(LearningObjective, LearningObjective.id == QuestionVersion.objective_id)
        .where(RoundItem.round_id == row.id, RoundItem.role == "main")
        .order_by(RoundItem.ordinal)
    ).all()
    history: dict[int, list[Attempt]] = {item.id: [] for item, *_ in items}
    for attempt in session.scalars(
        select(Attempt).where(Attempt.round_item_id.in_(history)).order_by(Attempt.id)
    ):
        history[attempt.round_item_id].append(attempt)
    finals = {item_id: rounds._final_attempt(tried) for item_id, tried in history.items()}
    stored = dict(
        session.execute(
            select(AttemptProcessing.attempt_id, AttemptProcessing.assessment_json).where(
                AttemptProcessing.attempt_id.in_([f.id for f in finals.values() if f is not None]),
                AttemptProcessing.status == "ready",
            )
        ).all()
    )
    waiting = False
    targets: list[WeakTarget] = []
    families: set[int] = set()
    for item, objective_id, family_id, family_topic_id, objective_topic_id in items:
        families.add(family_id)
        final = finals[item.id]
        if final is None:
            continue
        if final.processing_status == "processing" and not audio_overdue(final):
            waiting = True
        factual = _weak_factual(final, stored.get(final.id))
        if factual is not None:
            topic_id = objective_topic_id if objective_topic_id is not None else family_topic_id
            targets.append(WeakTarget(objective_id, topic_id, factual, item.ordinal))
    targets.sort(key=lambda t: (t.factual, t.ordinal))
    return waiting, targets, families


def _weak_factual(final: Attempt, assessment_json: str | None) -> float | None:
    """The factual score of an answer below Good, from its stored band; None otherwise."""
    if final.outcome in NO_EVIDENCE_OUTCOMES:
        return 0.0
    if final.outcome != "answered" or final.processing_status != "ready" or not assessment_json:
        return None
    try:
        assessment = json.loads(assessment_json)
    except ValueError:
        return None
    if assessment.get("status") != "assessed" or assessment.get("band") not in LOW_BANDS:
        return None
    return float((assessment.get("scores") or {}).get("factual") or 0.0)


def _weak_topics(session: Session, targets: list[WeakTarget]) -> list[WeakTopic]:
    ids = list(dict.fromkeys(t.topic_id for t in targets if t.topic_id is not None))
    if not ids:
        return []
    titles = dict(session.execute(select(Topic.id, Topic.title).where(Topic.id.in_(ids))).all())
    return [WeakTopic(topic_id=i, title=titles[i]) for i in ids if i in titles]


def _waiting_for_review(session: Session, subject_id: int, targets: list[WeakTarget]) -> int:
    topic_ids = [t.topic_id for t in targets if t.topic_id is not None]
    objective_ids = [t.objective_id for t in targets if t.objective_id is not None]
    return session.scalar(
        select(func.count())
        .select_from(QuestionVersion)
        .join(QuestionFamily, QuestionFamily.id == QuestionVersion.family_id)
        .where(
            QuestionFamily.subject_id == subject_id,
            QuestionVersion.status == "needs_review",
            or_(
                QuestionFamily.primary_topic_id.in_(topic_ids),
                QuestionVersion.objective_id.in_(objective_ids),
            ),
        )
    )


def _read(session: Session, round_id: int) -> _Reading:
    """Read-only: never settles a missing recording, and a fixed number of statements."""
    row = rounds._get_round(session, round_id)
    config = rounds._config(row)
    if not config.extension.enabled or row.round_type == "extension":
        return _Reading(row, config, "off")
    child = _child(session, round_id)
    if child is not None:
        return _Reading(row, config, "started", child=child)
    if row.status == "abandoned":
        return _Reading(row, config, "ended_early")
    if row.status in rounds.OPEN_STATUSES:
        return _Reading(row, config, "not_finished")
    waiting, targets, families = _main_answers(session, row)
    if waiting:
        return _Reading(row, config, "waiting")
    if not targets:
        return _Reading(row, config, "no_weak")
    reading = _Reading(row, config, "ready", targets=targets, families=families)
    reading.topics = _weak_topics(session, targets)
    candidates, _ = rounds._candidates(session, row.subject_id)
    requested = config.extension.question_count
    reading.available = len(
        select_extension(candidates, targets, config, requested, row.seed, families)
    )
    job = jobs.find_live_job(session, questions.GENERATE_KIND, subject_id=row.subject_id)
    reading.job_id = job.id if job is not None else None
    if reading.available == 0:
        reading.status = "generating" if job is not None else "no_questions"
        reading.needs_review = _waiting_for_review(session, row.subject_id, targets)
    elif reading.available < requested and job is not None:
        reading.status = "generating"
    return reading


def offer(session: Session, round_id: int) -> ExtensionOffer:
    reading = _read(session, round_id)
    return ExtensionOffer(
        round_id=round_id,
        status=reading.status,
        requested=reading.config.extension.question_count,
        available=reading.available,
        topics=reading.topics,
        job_id=reading.job_id,
        extension_round_id=reading.child.id if reading.child is not None else None,
        extension_status=reading.child.status if reading.child is not None else None,
        needs_review=reading.needs_review,
    )


def _extension_config(parent: RoundConfig, count: int) -> RoundConfig:
    """The parent's snapshot (scope, sources, timing, timers, audio, bands), N questions."""
    return parent.model_copy(
        update={
            "round_type": "extension",
            "question_count": count,
            "follow_ups": FollowUpConfig(
                enabled=False,
                prep_seconds=parent.follow_ups.prep_seconds,
                answer_seconds=parent.follow_ups.answer_seconds,
            ),
            "extension": parent.extension.model_copy(update={"enabled": False}),
        }
    )


def start(session: Session, settings: Settings, round_id: int) -> tuple[RoundState, bool]:
    """(the extension's state, whether it is new); a second start gets the same round."""
    reading = _read(session, round_id)
    if reading.child is not None:
        return rounds.get_state(session, reading.child.id), False
    if reading.status not in ("ready", "generating") or reading.available < 1:
        raise HTTPException(409, EXTENSION_NOT_NOW)
    row, parent = reading.row, reading.config
    candidates, rubric_ids = rounds._candidates(session, row.subject_id)
    seed = random.randrange(2**31)
    chosen = select_extension(
        candidates, reading.targets, parent, parent.extension.question_count, seed, reading.families
    )
    if not chosen:
        raise HTTPException(409, EXTENSION_NOT_NOW)
    config = _extension_config(parent, len(chosen))
    rounds.check_budget(session, settings, row.subject_id, config, len(chosen))
    try:
        child = rounds._insert_round(
            session, row.subject_id, config, chosen, rubric_ids, seed, parent_round_id=row.id
        )
        session.commit()
    except IntegrityError:
        # A parallel start (a double click) created it first: that one is the extension.
        session.rollback()
        existing = _child(session, round_id)
        if existing is None:
            raise
        return rounds.get_state(session, existing.id), False
    return rounds.get_state(session, child.id), True


def generation_policy(round_policy: str) -> str:
    """The questions an extension generates: an "All questions" round gets "Only my materials",
    the cheapest grounded choice, valid in its pool; other rounds keep their own policy."""
    return "materials" if round_policy == "all" else round_policy


def start_generation(
    session: Session,
    settings: Settings,
    round_id: int,
    generator: TextGenerator,
    background: BackgroundTasks,
) -> JobStarted:
    """New questions for the weak topics, when the bank has too few; a live job is reused."""
    reading = _read(session, round_id)
    short = (
        reading.status == "ready" and reading.available < reading.config.extension.question_count
    )
    if not (short or reading.status in ("no_questions", "generating")):
        raise HTTPException(409, EXTENSION_NOT_NOW)
    config = reading.config
    job, created = questions.start_generation_job(
        session,
        reading.row.subject_id,
        [t.topic_id for t in reading.topics],
        config.extension.question_count,
        config.style,
        config.difficulty,
        source_policy=generation_policy(config.source_policy),
    )
    if created:
        jobs.schedule(background, job, settings, generator)
    return JobStarted(job_id=job.id)
