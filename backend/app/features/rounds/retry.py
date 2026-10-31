"""End-of-round retry round: the round's answers below Good, asked once more.

The retry round is a linked round (round_type "retry", parent_round_id): it reuses the whole
phase machine with the original items' question and rubric versions. Its answers are retries
(assisted evidence in coverage) and keep their own score; the original round never changes.
"""

import json
import random
from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.features.attempts.models import AttemptProcessing
from app.features.attempts.service import audio_overdue
from app.features.rounds import service as rounds
from app.features.rounds.extension import _weak_factual
from app.features.rounds.models import Round, RoundItem
from app.features.rounds.schemas import (
    RETRY_NOT_NOW,
    FollowUpConfig,
    RetryOffer,
    RetryStatus,
    RoundConfig,
    RoundState,
)

NO_CHAIN_TYPES = ("retry", "extension")  # a retry round or an extension offers no retry round


@dataclass
class _Reading:
    """Everything one offer read finds; start acts on the same reading."""

    row: Round
    config: RoundConfig
    status: RetryStatus
    child: Round | None = None
    picks: list[RoundItem] = field(default_factory=list)
    unresolved: int = 0


def _child(session: Session, round_id: int) -> Round | None:
    return session.scalar(
        select(Round).where(Round.parent_round_id == round_id, Round.round_type == "retry")
    )


def _children(session: Session, round_id: int) -> dict[str, Round]:
    """The round's linked rounds by type (at most one each: uq_round_parent_type)."""
    return {
        child.round_type: child
        for child in session.scalars(select(Round).where(Round.parent_round_id == round_id))
    }


def _main_answers(session: Session, row: Round) -> tuple[bool, list[RoundItem], int]:
    """(waiting, items below Good, unresolved) from three statements, whatever the round size.

    Each item is judged by its original final answer (never its repeat) and the band stored
    with it, never re-classified with today's band table."""
    items = rounds._items(session, row.id)
    history = rounds._attempts(session, items)
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
    picks: list[RoundItem] = []
    unresolved = 0
    for item in items:
        final = finals[item.id]
        if final is None or final.outcome in ("pending", "interrupted"):
            continue  # not asked, or only interrupted
        if final.processing_status == "processing" and not audio_overdue(final):
            waiting = True
        if _weak_factual(final, stored.get(final.id)) is not None:
            picks.append(item)
        elif _unresolved(final.outcome, final.processing_status, stored.get(final.id)):
            unresolved += 1
    return waiting, picks, unresolved


def _unresolved(outcome: str, processing_status: str | None, assessment_json: str | None) -> bool:
    """An answer that could not be assessed: a failure, missing audio or no speech."""
    if outcome == "technical_failure" or processing_status in ("failed", "processing"):
        return True
    if outcome != "answered" or not assessment_json:
        return False
    try:
        return json.loads(assessment_json).get("status") == "no_speech"
    except ValueError:
        return False


def _read(session: Session, round_id: int) -> _Reading:
    """Read-only: never settles a missing recording, and a fixed number of statements."""
    row = rounds._get_round(session, round_id)
    config = rounds._config(row)
    if not config.retry_round or row.round_type in NO_CHAIN_TYPES:
        return _Reading(row, config, "off")
    children = _children(session, round_id)
    if "retry" in children:
        return _Reading(row, config, "started", child=children["retry"])
    if row.status == "abandoned":
        return _Reading(row, config, "ended_early")
    if row.status in rounds.OPEN_STATUSES:
        return _Reading(row, config, "not_finished")
    waiting, picks, unresolved = _main_answers(session, row)
    if waiting:
        return _Reading(row, config, "waiting", unresolved=unresolved)
    extension = children.get("extension")
    if extension is not None and extension.status in rounds.OPEN_STATUSES:
        return _Reading(row, config, "extension_open", picks=picks, unresolved=unresolved)
    if not picks:
        return _Reading(row, config, "none_below", unresolved=unresolved)
    return _Reading(row, config, "ready", picks=picks, unresolved=unresolved)


def offer(session: Session, round_id: int) -> RetryOffer:
    reading = _read(session, round_id)
    return RetryOffer(
        round_id=round_id,
        status=reading.status,
        count=len(reading.picks),
        ordinals=[item.ordinal + 1 for item in reading.picks],
        unresolved=reading.unresolved,
        retry_round_id=reading.child.id if reading.child is not None else None,
        retry_status=reading.child.status if reading.child is not None else None,
    )


def _retry_config(parent: RoundConfig, count: int) -> RoundConfig:
    """The parent's snapshot (scope, sources, timing, timers, audio, bands); no follow-ups,
    no extension, no Answer again and no further retry round."""
    return parent.model_copy(
        update={
            "round_type": "retry",
            "question_count": count,
            "follow_ups": FollowUpConfig(
                enabled=False,
                prep_seconds=parent.follow_ups.prep_seconds,
                answer_seconds=parent.follow_ups.answer_seconds,
            ),
            "extension": parent.extension.model_copy(update={"enabled": False}),
            "immediate_repeat": False,
            "retry_round": False,
        }
    )


def start(
    session: Session, round_id: int, settings: Settings | None = None
) -> tuple[RoundState, bool]:
    """(the retry round's state, whether it is new); a second start gets the same round."""
    reading = _read(session, round_id)
    if reading.child is not None:
        return rounds.get_state(session, reading.child.id), False
    if reading.status != "ready":
        raise HTTPException(409, RETRY_NOT_NOW)
    row, picks = reading.row, reading.picks
    config = _retry_config(reading.config, len(picks))
    if settings is not None:
        rounds.check_budget(session, settings, row.subject_id, config, len(picks))
    try:
        child = rounds._insert_round(
            session,
            row.subject_id,
            config,
            [item.question_version_id for item in picks],
            {item.question_version_id: item.rubric_version_id for item in picks},
            random.randrange(2**31),
            parent_round_id=row.id,
            parent_item_ids=[item.id for item in picks],
        )
        session.commit()
    except IntegrityError:
        # A parallel start (a double click) created it first: that one is the retry round.
        session.rollback()
        existing = _child(session, round_id)
        if existing is None:
            raise
        return rounds.get_state(session, existing.id), False
    return rounds.get_state(session, child.id), True
