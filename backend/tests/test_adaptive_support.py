"""Builders for the adaptive and extension tests and the e2e seed (no tests here)."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from test_assessment_support import metrics
from test_attempts_support import ai_for
from test_coverage_support import (
    FACTUAL_FOR_BAND,
    make_answer,
    make_objective,
    make_objective_question,
    make_round,
)
from test_topics_support import make_subject, make_topic, make_version

from app.features.assessment.schemas import AssessmentResult, Scores
from app.features.assessment.scoring import model_answer_display
from app.features.attempts.models import AttemptProcessing
from app.features.library.models import Subject
from app.features.questions.models import QuestionVersion
from app.features.rounds.models import Attempt, RoundItem
from app.features.topics.models import LearningObjective, Topic

TOPICS = (
    ("Ostracyzm: procedura i cel", "Wyjaśnia procedurę ostracyzmu."),
    ("Reformy Solona", "Omawia reformy Solona."),
    ("Reformy Klejstenesa", "Opisuje reformy Klejstenesa."),
)


@dataclass
class World:
    subject: Subject
    topics: list[Topic]
    objectives: list[LearningObjective]
    questions: dict[int, list[QuestionVersion]] = field(default_factory=dict)  # objective index

    def objective_of(self, question_version_id: int) -> int:
        for index, items in self.questions.items():
            if any(q.id == question_version_id for q in items):
                return index
        raise KeyError(question_version_id)


def build_world(
    session: Session,
    *,
    name: str = "Historia",
    per_objective: tuple[int, ...] = (4, 4, 4),
    style: str = "short",
) -> World:
    """Three topics with one objective each and their questions; no evidence."""
    subject = make_subject(session, name)
    _, passages = make_version(session, subject.id, count=4, excluded=())
    world = World(subject=subject, topics=[], objectives=[])
    for n, ((title, text), count) in enumerate(zip(TOPICS, per_objective, strict=True)):
        topic = make_topic(session, subject.id, title, passages[:2], ordinal=n)
        objective = make_objective(session, topic, text)
        world.topics.append(topic)
        world.objectives.append(objective)
        world.questions[n] = []
        for k in range(count):
            question = make_objective_question(
                session, subject.id, topic.id, objective.id, f"Pytanie {k + 1}: {text}"
            )
            question.style = style
            world.questions[n].append(question)
    session.commit()
    return world


def adaptive_world(session: Session, now: datetime, **kwargs) -> World:
    """O1 Weak (one day ago), O2 Very good on two days (Strong), O3 never answered."""
    world = build_world(session, **kwargs)
    weak = make_round(session, world.subject.id, at=now - timedelta(days=1))
    make_answer(session, weak, world.questions[0][0], at=now - timedelta(days=1), band="weak")
    for days in (3, 2):
        at = now - timedelta(days=days)
        strong = make_round(session, world.subject.id, at=at)
        make_answer(session, strong, world.questions[1][0], at=at, band="very_good")
    return world


def answer_item(
    session: Session,
    item: RoundItem,
    *,
    at: datetime,
    band: str | None = None,
    factual: float | None = None,
    outcome: str = "answered",
    processing: str = "ready",
    assessment_status: str = "assessed",
) -> Attempt:
    """Fills the item's pending (or still processing) attempt, with its stored result as
    make_answer writes it; an item without an attempt gets its initial one."""
    attempt = session.scalar(
        select(Attempt)
        .where(
            Attempt.round_item_id == item.id,
            or_(Attempt.outcome == "pending", Attempt.processing_status == "processing"),
        )
        .order_by(Attempt.id.desc())
        .limit(1)
    )
    if attempt is None:
        attempt = Attempt(round_item_id=item.id, attempt_kind="initial", outcome="pending")
        session.add(attempt)
    answered = outcome == "answered"
    attempt.outcome = outcome
    attempt.processing_status = processing if answered else None
    attempt.started_at = at
    attempt.ended_at = at
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
            question_version_id=item.question_version_id,
            provenance=None,
            attempts_used=1,
        )
        session.add(
            AttemptProcessing(
                attempt_id=attempt.id,
                status="ready",
                stage="done",
                transcript_text="Odpowiedź ucznia.",
                metrics_json=metrics().model_dump_json(),
                assessment_json=result.model_dump_json(),
            )
        )
    session.commit()
    return attempt
