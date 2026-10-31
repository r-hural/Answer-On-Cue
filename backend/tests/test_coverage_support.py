"""Builders for the coverage tests and the e2e seed (no tests here)."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_assessment_support import metrics
from test_attempts_support import ai_for
from test_topics_support import make_question

from app.features.assessment.schemas import AssessmentResult, Scores
from app.features.assessment.scoring import model_answer_display
from app.features.attempts.models import AttemptProcessing
from app.features.questions.models import QuestionVersion, RubricVersion
from app.features.rounds.models import Attempt, Round, RoundItem
from app.features.rounds.schemas import RoundConfig, Scope
from app.features.topics.models import LearningObjective

FACTUAL_FOR_BAND = {
    "weak": 20.0,
    "needs_improvement": 45.0,
    "good": 66.0,
    "very_good": 88.0,
    None: 0.0,
}


def make_objective(session: Session, topic, text: str, ordinal: int = 0) -> LearningObjective:
    objective = LearningObjective(topic_id=topic.id, text=text, ordinal=ordinal)
    session.add(objective)
    session.commit()
    return objective


def make_objective_question(
    session: Session,
    subject_id: int,
    topic_id: int | None,
    objective_id: int | None,
    text: str,
    *,
    passage_ids: tuple[int, ...] = (),
    status: str = "active",
    source_policy: str = "materials",
    difficulty: str = "recall",
) -> QuestionVersion:
    question = make_question(
        session, subject_id, topic_id, text, status=status, passage_ids=passage_ids
    )
    question.objective_id = objective_id
    question.source_policy = source_policy
    question.difficulty = difficulty
    session.commit()
    return question


def make_round(
    session: Session,
    subject_id: int,
    *,
    at: datetime,
    timing: str = "practice",
    status: str = "finished",
    phase: str | None = None,
    current_index: int = 0,
) -> Round:
    config = RoundConfig(
        scope=Scope(kind="subject"),
        feedback_timing=timing,
        band_bounds=(30, 60, 80),
        factual_weights=(30, 50),
    )
    finished = status in ("finished", "abandoned")
    row = Round(
        subject_id=subject_id,
        round_type="standard",
        status=status,
        phase=phase or ("summary" if finished else "question"),
        current_index=current_index,
        config_json=config.model_dump_json(),
        seed=1,
        started_at=at,
        finished_at=at if status == "finished" else None,
    )
    session.add(row)
    session.commit()
    return row


def make_answer(
    session: Session,
    round_: Round,
    question: QuestionVersion,
    *,
    at: datetime,
    band: str | None = None,
    factual: float | None = None,
    kind: str = "initial",
    outcome: str = "answered",
    processing: str | None = "ready",
    assessment_status: str = "assessed",
    prep_ms: int = 8000,
    role: str = "main",
    ordinal: int | None = None,
) -> Attempt:
    if ordinal is None:
        last = session.scalar(
            select(func.max(RoundItem.ordinal)).where(
                RoundItem.round_id == round_.id, RoundItem.role == role
            )
        )
        ordinal = 0 if last is None else last + 1
    rubric_id = session.scalar(
        select(RubricVersion.id).where(RubricVersion.question_version_id == question.id)
    )
    item = RoundItem(
        round_id=round_.id,
        ordinal=ordinal,
        role=role,
        question_version_id=question.id,
        rubric_version_id=rubric_id,
    )
    session.add(item)
    session.flush()
    answered = outcome == "answered"
    attempt = Attempt(
        round_item_id=item.id,
        attempt_kind=kind,
        outcome=outcome,
        processing_status=processing if answered else None,
        started_at=at,
        ended_at=at,
    )
    session.add(attempt)
    session.flush()
    if answered and processing == "ready":
        value = FACTUAL_FOR_BAND[band] if factual is None else factual
        result = AssessmentResult(
            status=assessment_status,
            scores=Scores(
                correctness=value, completeness=value, factual=value, language=70.0, total=value
            ),
            band=band,
            model_answer_display=model_answer_display(band, assessment_status),
            ai=ai_for(),
            band_bounds=(30, 60, 80),
            factual_weights=(30, 50),
            prompt_version="test-v1",
            question_version_id=question.id,
            provenance=None,
            attempts_used=1,
        )
        session.add(
            AttemptProcessing(
                attempt_id=attempt.id,
                status="ready",
                stage="done",
                transcript_text="Odpowiedź ucznia.",
                metrics_json=metrics(preparation_used_ms=prep_ms).model_dump_json(),
                assessment_json=result.model_dump_json(),
            )
        )
    elif answered and processing == "failed":
        session.add(
            AttemptProcessing(
                attempt_id=attempt.id,
                status="failed",
                stage="transcription",
                failure_kind="transcription",
                failure_message="The transcription failed.",
            )
        )
    session.commit()
    return attempt
