"""The A/B inputs, read from the database copy: deterministic, and Polish only."""

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.assessment.schemas import AssessmentInput, AssessmentResult
from app.features.attempts.models import AttemptProcessing
from app.features.delivery.schemas import AnswerMetrics
from app.features.follow_ups import service as follow_ups
from app.features.library.models import Document, DocumentVersion, Subject
from app.features.questions import service as questions
from app.features.questions.schemas import QuestionWithRubric
from app.features.rounds import service as rounds
from app.features.rounds.models import Attempt, Round, RoundItem
from app.features.topics import service as topics
from app.features.topics.models import LearningObjective, Topic

BANK_SIZE = 5
ASSESSMENT_COUNT = 3
POLICY = "materials"


class Refused(Exception):
    """The tool will not run this: nothing was called."""


@dataclass(frozen=True)
class ExtractionInput:
    version_id: int
    passages: list[tuple[int, str]]


@dataclass(frozen=True)
class BankInput:
    topic_id: int
    title: str
    description: str | None
    objectives: list[str]
    passages: list[tuple[int, str]]
    existing: list[str]
    slots: list[tuple[str, str]]
    allowed: set[int]
    policy: str = POLICY


@dataclass(frozen=True)
class AssessmentCase:
    attempt_id: int
    inp: AssessmentInput
    stored: AssessmentResult


@dataclass(frozen=True)
class FollowUpInput:
    attempt_id: int
    policy: str
    question: QuestionWithRubric
    passages: list[tuple[int, str]]
    transcript: str
    points: list[tuple[str, str, str]]
    mistakes: list[tuple[str, str]]


@dataclass
class AbInputs:
    subject_id: int
    subject_name: str
    extraction: ExtractionInput | None = None
    bank: BankInput | None = None
    assessments: list[AssessmentCase] = field(default_factory=list)
    follow_up: FollowUpInput | None = None
    missing: dict[str, str] = field(default_factory=dict)


def _assessed_rows(session: Session, subject_id: int | None = None) -> list[tuple]:
    """(attempt id, subject id, processing row) of the answers with a transcript, metrics and
    an assessed result, newest first."""
    query = (
        select(Attempt.id, Round.subject_id, AttemptProcessing)
        .join(AttemptProcessing, AttemptProcessing.attempt_id == Attempt.id)
        .join(RoundItem, RoundItem.id == Attempt.round_item_id)
        .join(Round, Round.id == RoundItem.round_id)
        .where(
            AttemptProcessing.transcript_text.is_not(None),
            AttemptProcessing.metrics_json.is_not(None),
            AttemptProcessing.assessment_json.is_not(None),
        )
        .order_by(Attempt.id.desc())
    )
    if subject_id is not None:
        query = query.where(Round.subject_id == subject_id)
    found = []
    for attempt_id, sid, row in session.execute(query):
        if not (row.transcript_text or "").strip():
            continue
        result = AssessmentResult.model_validate_json(row.assessment_json)
        if result.status == "assessed" and result.ai is not None:
            found.append((attempt_id, sid, row))
    return found


def _subject(session: Session, subject_id: int | None) -> Subject:
    if subject_id is not None:
        subject = session.get(Subject, subject_id)
        if subject is None:
            raise Refused(f"Subject {subject_id} is not in this database.")
        if subject.language != "pl":
            raise Refused(
                f"Subject {subject_id} is not Polish ({subject.language}): "
                "the old prompts are Polish only."
            )
        return subject
    polish = list(
        session.scalars(select(Subject).where(Subject.language == "pl").order_by(Subject.id))
    )
    if not polish:
        raise Refused("This database has no Polish subject.")
    counts = Counter(sid for _, sid, _ in _assessed_rows(session))
    return max(polish, key=lambda s: (counts[s.id], -s.id))


def _extraction(session: Session, subject_id: int) -> ExtractionInput | None:
    version = session.scalar(
        select(DocumentVersion)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(Document.subject_id == subject_id, DocumentVersion.status == "imported")
        .order_by(DocumentVersion.id.desc())
        .limit(1)
    )
    if version is None:
        return None
    for section in topics.extraction_sections(session, version):
        passages = topics.usable_passages(session, version.id, section)
        if passages:
            return ExtractionInput(version.id, [(p.id, p.text) for p in passages.values()])
    chosen: list[tuple[int, str]] = []
    used = 0
    for passage in topics.usable_passages(session, version.id).values():
        if used + len(passage.text) > topics.SECTION_CHAR_LIMIT:
            break
        chosen.append((passage.id, passage.text))
        used += len(passage.text)
    return ExtractionInput(version.id, chosen) if chosen else None


def _bank(session: Session, subject_id: int) -> BankInput | None:
    active = session.scalars(
        select(Topic)
        .where(Topic.subject_id == subject_id, Topic.status == "active")
        .order_by(Topic.ordinal, Topic.id)
    )
    counted = [(len(questions.allowed_passage_ids(session, t.id)), t) for t in active]
    counted = [(n, t) for n, t in counted if n]
    if not counted:
        return None
    most = max(n for n, _ in counted)
    topic = next(t for n, t in counted if n == most)  # the first in (ordinal, id) order
    objectives = session.scalars(
        select(LearningObjective.text)
        .where(LearningObjective.topic_id == topic.id)
        .order_by(LearningObjective.ordinal, LearningObjective.id)
    )
    return BankInput(
        topic_id=topic.id,
        title=topic.title,
        description=topic.description,
        objectives=list(objectives),
        passages=[(p.id, p.text) for p in questions.topic_passages(session, topic.id)],
        existing=questions.topic_question_texts(session, topic.id),
        slots=questions.slot_specs("mixed", "mixed", 0, BANK_SIZE),
        allowed=questions.allowed_passage_ids(session, topic.id),
    )


def _assessments(session: Session, subject_id: int) -> list[AssessmentCase]:
    """Each answer rebuilt as the attempt pipeline builds it."""
    cases = []
    for attempt_id, _, row in _assessed_rows(session, subject_id)[:ASSESSMENT_COUNT]:
        ctx = rounds.get_attempt_context(session, attempt_id)
        question = questions.get_question(session, ctx.question_version_id, with_rubric=True)
        inp = AssessmentInput(
            question=question,
            transcript=row.transcript_text,
            outcome="answered",
            attempt_kind=ctx.attempt_kind,
            interrupted=False,
            delivery=AnswerMetrics.model_validate_json(row.metrics_json),
        )
        stored = AssessmentResult.model_validate_json(row.assessment_json)
        cases.append(AssessmentCase(attempt_id, inp, stored))
    return cases


def _follow_up(session: Session, case: AssessmentCase) -> FollowUpInput:
    """The follow-up input as follow_ups.service builds it, from the stored assessment."""
    ctx = rounds.get_attempt_context(session, case.attempt_id)
    main = case.inp.question
    texts = {p.id: p.text for p in main.rubric.essential_points}
    ai = case.stored.ai
    return FollowUpInput(
        attempt_id=case.attempt_id,
        policy=main.source_policy,
        question=main,
        passages=follow_ups._passages(session, main, ctx.config),
        transcript=case.inp.transcript or "",
        points=[(texts.get(p.point_id, p.point_id), p.status, p.note) for p in ai.points],
        mistakes=[(m.quote, m.correction) for m in ai.factual_mistakes],
    )


def select_inputs(session: Session, subject_id: int | None) -> AbInputs:
    subject = _subject(session, subject_id)
    found = AbInputs(subject_id=subject.id, subject_name=subject.name)
    found.extraction = _extraction(session, subject.id)
    if found.extraction is None:
        found.missing["extraction"] = "no imported document with usable passages"
    found.bank = _bank(session, subject.id)
    if found.bank is None:
        found.missing["bank"] = "no active topic with usable passages"
    found.assessments = _assessments(session, subject.id)
    if found.assessments:
        found.follow_up = _follow_up(session, found.assessments[0])
    else:
        found.missing["assessments"] = "no assessed answers"
        found.missing["follow_up"] = "no assessed answers"
    return found
