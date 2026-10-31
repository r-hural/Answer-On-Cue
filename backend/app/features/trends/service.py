"""Trends read model: computed on every read from the rows of rounds and attempts.

One batched, read-only query. Nothing here calls the attempts, reports or rounds services: a
trends GET must never settle a missing recording, and those functions may change.
"""

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.features.attempts.models import AttemptProcessing
from app.features.library.models import Subject
from app.features.questions.models import QuestionVersion
from app.features.rounds.models import Attempt, Round, RoundItem
from app.features.rounds.schemas import RoundConfig
from app.features.trends import series
from app.features.trends.schemas import TrendFilters, TrendsOut
from app.features.trends.series import TRENDS_LIMIT, TrendRow

SUBJECT_NOT_FOUND = "Subject not found."
DEFAULT_POLICY = RoundConfig.model_fields["source_policy"].default
# "All questions" covers rounds stored "all" and legacy "expanded" rounds.
GROUPS = {"materials": {"materials"}, "expanded": {"expanded"}, "all": {"all", "expanded"}}


def _loads(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _policy(config_json: str) -> str:
    """The round's comparison group: its RoundConfig.source_policy, as History uses."""
    return (_loads(config_json) or {}).get("source_policy", DEFAULT_POLICY)


def load_rows(
    session: Session,
    subject_id: int,
    source_policy: str,
    style: str | None,
    difficulty: str | None,
) -> list[TrendRow]:
    """One statement: the settled attempts of the subject's finished and abandoned rounds.

    The comparison group is read from the round's config in the same rows, never by a further
    statement per round.
    """
    stmt = (
        select(
            Attempt.id,
            RoundItem.round_id,
            Round.round_type,
            Round.status,
            Round.started_at,
            Round.config_json,
            RoundItem.id,
            RoundItem.role,
            Attempt.attempt_kind,
            Attempt.outcome,
            AttemptProcessing.status,
            AttemptProcessing.assessment_json,
            AttemptProcessing.metrics_json,
            RoundItem.question_version_id,
        )
        .select_from(Attempt)
        .join(RoundItem, RoundItem.id == Attempt.round_item_id)
        .join(Round, Round.id == RoundItem.round_id)
        .join(QuestionVersion, QuestionVersion.id == RoundItem.question_version_id)
        .outerjoin(AttemptProcessing, AttemptProcessing.attempt_id == Attempt.id)
        .where(
            Round.subject_id == subject_id,
            Round.status.in_(series.SHOWN_STATUSES),
            Attempt.outcome != "pending",
        )
        .order_by(Round.started_at, Round.id, RoundItem.id, Attempt.id)
    )
    if style is not None:
        stmt = stmt.where(QuestionVersion.style == style)
    if difficulty is not None:
        stmt = stmt.where(QuestionVersion.difficulty == difficulty)
    return [
        TrendRow(
            attempt_id=attempt_id,
            round_id=round_id,
            round_type=round_type,
            round_status=round_status,
            started_at=started_at,
            config_json=config_json,
            item_id=item_id,
            role=role,
            kind=kind,
            outcome=outcome,
            processing=processing,
            assessment=_loads(assessment_json),
            metrics=_loads(metrics_json),
            question_version_id=question_version_id,
        )
        for (
            attempt_id,
            round_id,
            round_type,
            round_status,
            started_at,
            config_json,
            item_id,
            role,
            kind,
            outcome,
            processing,
            assessment_json,
            metrics_json,
            question_version_id,
        ) in session.execute(stmt)  # fmt: skip
        if _policy(config_json) in GROUPS[source_policy]
    ]


def trends(session: Session, settings: Settings, filters: TrendFilters) -> TrendsOut:
    subject = session.get(Subject, filters.subject_id)
    if subject is None:
        raise HTTPException(404, SUBJECT_NOT_FOUND)
    rows = load_rows(
        session, filters.subject_id, filters.source_policy, filters.style, filters.difficulty
    )
    chosen = series.select_answers(rows, filters)
    ordered = sorted(
        chosen.values(), key=lambda answers: (answers[0].row.started_at, answers[0].row.round_id)
    )[-TRENDS_LIMIT:]
    points = [series.point_for(answers, filters.include_interrupted) for answers in ordered]
    series.mark_changes(
        points,
        [series.models_of(answers) for answers in ordered],
        [series.detectors_of(answers) for answers in ordered],
    )
    return TrendsOut(
        subject_id=subject.id,
        subject_name=subject.name,
        filters=filters,
        band_bounds=settings.factual_band_bounds,
        points=points,
    )
