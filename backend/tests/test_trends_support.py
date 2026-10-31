"""Builders for the trends tests and the e2e seed (no tests here)."""

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session
from test_assessment_support import metrics as _metrics
from test_attempts_support import ai_for

from app.features.assessment.schemas import AssessmentResult, Scores
from app.features.assessment.scoring import model_answer_display
from app.features.attempts.models import AttemptProcessing
from app.features.providers.types import Provenance
from app.features.rounds.models import Attempt, RoundItem
from app.features.trends.series import TrendRow

T0 = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
_ids = iter(range(1, 10**6))


def metrics(**over) -> dict:
    return json.loads(_metrics(**over).model_dump_json())


def assessment(
    band="good",
    factual=66.0,
    *,
    total=None,
    language=70.0,
    fluency=None,
    coverage="full",
    doubts=0,
    model="m1",
    status="assessed",
    bounds=(30, 60, 80),
) -> dict:
    from app.features.assessment.schemas import TranscriptionDoubt

    ai = ai_for(
        fluency=fluency,
        fluency_coverage=coverage,
        transcription_doubts=[TranscriptionDoubt(quote="x", reason="r") for _ in range(doubts)],
    )
    result = AssessmentResult(
        status=status,
        scores=Scores(
            correctness=factual,
            completeness=factual,
            factual=factual,
            language=language,
            total=factual if total is None else total,
        ),
        band=band,
        model_answer_display=model_answer_display(band, status),
        ai=ai,
        band_bounds=bounds,
        factual_weights=(30, 50),
        prompt_version="test-v1",
        question_version_id=1,
        provenance=Provenance(provider="openai", model=model, operation_id="op"),
        attempts_used=1,
    )
    return json.loads(result.model_dump_json())


def row(
    *,
    round_id=1,
    item_id=None,
    kind="initial",
    outcome="answered",
    role="main",
    round_type="standard",
    round_status="finished",
    at=T0,
    bounds=(30, 60, 80),
    timing="practice",
    processing="ready",
    assess="default",
    metr="default",
    question_version_id=1,
) -> TrendRow:
    attempt_id = next(_ids)
    config = {"band_bounds": list(bounds), "factual_weights": [30, 50], "feedback_timing": timing}
    answered = outcome == "answered"
    return TrendRow(
        attempt_id=attempt_id,
        round_id=round_id,
        round_type=round_type,
        round_status=round_status,
        started_at=at,
        config_json=json.dumps(config),
        item_id=attempt_id if item_id is None else item_id,
        role=role,
        kind=kind,
        outcome=outcome,
        processing=processing if answered else None,
        assessment=(assessment() if assess == "default" else assess) if answered else None,
        metrics=(metrics() if metr == "default" else metr) if answered else None,
        question_version_id=question_version_id,
    )


def add_attempt(
    session: Session,
    item_id: int,
    kind: str,
    outcome: str = "answered",
    *,
    at: datetime,
    factual: float | None = None,
    metrics_over: dict | None = None,
) -> Attempt:
    """A further attempt on an existing round item, with a ready assessment when answered."""
    item = session.get(RoundItem, item_id)
    attempt = Attempt(
        round_item_id=item_id,
        attempt_kind=kind,
        outcome=outcome,
        processing_status="ready" if outcome == "answered" else None,
        started_at=at,
        ended_at=at,
    )
    session.add(attempt)
    session.flush()
    if outcome == "answered":
        value = 66.0 if factual is None else factual
        session.add(
            AttemptProcessing(
                attempt_id=attempt.id,
                status="ready",
                stage="done",
                transcript_text="Odpowiedź ucznia.",
                metrics_json=json.dumps(metrics(**(metrics_over or {}))),
                assessment_json=json.dumps(
                    assessment(factual=value, bounds=(30, 60, 80))
                    | {"question_version_id": item.question_version_id}
                ),
            )
        )
    session.commit()
    return attempt


def item_of(session: Session, attempt: Attempt) -> int:
    return session.scalar(select(RoundItem.id).where(RoundItem.id == attempt.round_item_id))
