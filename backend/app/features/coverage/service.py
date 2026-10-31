"""Coverage read model: computed on every read from the rows of rounds, attempts and topics.

Nothing here writes, and nothing calls the attempts or rounds services: a coverage GET must never
settle a missing recording, and those functions may change.
"""

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.features.attempts.models import AttemptProcessing
from app.features.coverage.rules import (
    INDEPENDENT_KINDS,
    CoverageStatus,
    Evidence,
    status_for,
    topic_status,
)
from app.features.coverage.schemas import (
    EvidenceOut,
    HistoryEntry,
    ObjectiveCoverage,
    SubjectCoverage,
    TopicCoverage,
    TopicDetail,
    TopicSource,
)
from app.features.library.models import Document, DocumentVersion, SourcePassage, Subject
from app.features.questions.models import QuestionFamily, QuestionVersion
from app.features.rounds.models import Attempt, Round, RoundItem
from app.features.topics.models import LearningObjective, Term, Topic, TopicPassage

SUBJECT_NOT_FOUND = "Subject not found."
LIVE_STATUSES = ("active", "needs_review")
WITHHELD_STATUSES = ("active", "paused")
STATUS_ORDER: tuple[CoverageStatus, ...] = ("strong", "improving", "needs_work", "not_practised")
RECENT = 3

LineKey = tuple[int, int | None]  # (topic id, objective id); None is the whole-topic line


def _utc(value: datetime) -> datetime:
    # SQLite hands timestamps back naive; they were written as UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class AttemptRow:
    """One main attempt that may be shown, with the stored result it produced."""

    attempt_id: int
    round_id: int
    kind: str
    outcome: str
    processing_status: str | None
    at: datetime
    question_id: int
    question_text: str
    question_status: str
    style: str
    difficulty: str
    objective_id: int | None
    family_topic_id: int | None
    processing: str | None  # attempt_processing.status
    assessment: dict[str, Any] | None
    metrics: dict[str, Any] | None

    @property
    def independent(self) -> bool:
        return self.kind in INDEPENDENT_KINDS


def _loads(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _hidden(config_json: str, status: str, phase: str, ordinal: int, current_index: int) -> bool:
    """A running round keeps its answers back until their feedback is shown."""
    if status not in WITHHELD_STATUSES:
        return False
    config = _loads(config_json) or {}
    if config.get("feedback_timing") == "exam":
        return True
    return ordinal == current_index and phase != "feedback"


def load_attempts(
    session: Session,
    subject_id: int,
    objective_ids: list[int] | None = None,
    topic_id: int | None = None,
    layer: str = "materials",
) -> list[AttemptRow]:
    """One statement: the main attempts of the subject (or of one topic) that may be shown.

    For a topic, `objective_ids` are its objectives and `topic_id` its id: a question belongs to
    it by its objective, or without an objective by the family's primary topic while the question
    is live (a retired or `followup` objective-less question has no line, so no history either).
    `layer` is the source policy of the questions counted: each layer is separate;
    "all" counts every policy.
    """
    stmt = (
        select(
            Attempt.id,
            RoundItem.round_id,
            Attempt.attempt_kind,
            Attempt.outcome,
            Attempt.processing_status,
            Attempt.ended_at,
            Attempt.started_at,
            QuestionVersion.id,
            QuestionVersion.text,
            QuestionVersion.status,
            QuestionVersion.style,
            QuestionVersion.difficulty,
            QuestionVersion.objective_id,
            QuestionFamily.primary_topic_id,
            AttemptProcessing.status,
            AttemptProcessing.assessment_json,
            AttemptProcessing.metrics_json,
            Round.config_json,
            Round.status,
            Round.phase,
            Round.current_index,
            RoundItem.ordinal,
        )
        .select_from(Attempt)
        .join(RoundItem, RoundItem.id == Attempt.round_item_id)
        .join(Round, Round.id == RoundItem.round_id)
        .join(QuestionVersion, QuestionVersion.id == RoundItem.question_version_id)
        .join(QuestionFamily, QuestionFamily.id == QuestionVersion.family_id)
        .outerjoin(AttemptProcessing, AttemptProcessing.attempt_id == Attempt.id)
        .where(
            Round.subject_id == subject_id,
            RoundItem.role == "main",
            Attempt.attempt_kind != "followup",
            Attempt.outcome != "pending",
        )
    )
    if layer != "all":
        stmt = stmt.where(QuestionVersion.source_policy == layer)
    if topic_id is not None:
        # Without an objective, only a live question belongs to the topic (as in `line_key`).
        in_topic = (
            QuestionVersion.objective_id.is_(None)
            & (QuestionFamily.primary_topic_id == topic_id)
            & QuestionVersion.status.in_(LIVE_STATUSES)
        )
        if objective_ids:
            in_topic = in_topic | QuestionVersion.objective_id.in_(objective_ids)
        stmt = stmt.where(in_topic)
    rows = []
    for (
        attempt_id, round_id, kind, outcome, processing_status, ended_at, started_at,
        question_id, text, question_status, style, difficulty, objective_id, family_topic_id,
        processing, assessment_json, metrics_json, config_json, round_status, phase,
        current_index, ordinal,
    ) in session.execute(stmt):  # fmt: skip
        if _hidden(config_json, round_status, phase, ordinal, current_index):
            continue
        rows.append(
            AttemptRow(
                attempt_id=attempt_id,
                round_id=round_id,
                kind=kind,
                outcome=outcome,
                processing_status=processing_status,
                at=_utc(ended_at or started_at),
                question_id=question_id,
                question_text=text,
                question_status=question_status,
                style=style,
                difficulty=difficulty,
                objective_id=objective_id,
                family_topic_id=family_topic_id,
                processing=processing,
                assessment=_loads(assessment_json),
                metrics=_loads(metrics_json),
            )
        )
    return rows


def _evidence(row: AttemptRow) -> Evidence | None:
    """The knowledge evidence of an attempt, or None when it does not count."""
    if row.outcome == "skipped" and not row.independent:
        return None  # skipping an offered repeat or retry is not a failed answer
    if row.outcome in ("dont_know", "skipped"):
        return Evidence(row.attempt_id, row.round_id, row.at, "weak", 0.0, row.independent)
    if row.outcome != "answered" or row.processing != "ready" or row.assessment is None:
        return None
    if row.assessment.get("status") != "assessed" or row.assessment.get("band") is None:
        return None
    scores = row.assessment.get("scores") or {}
    prep = (row.metrics or {}).get("preparation_used_ms")
    return Evidence(
        row.attempt_id,
        row.round_id,
        row.at,
        row.assessment["band"],
        float(scores.get("factual", 0.0)),
        row.independent,
        prep if isinstance(prep, int) else None,
    )


def evidence_out(item: Evidence | None) -> EvidenceOut | None:
    if item is None:
        return None
    return EvidenceOut(
        attempt_id=item.attempt_id,
        round_id=item.round_id,
        at=_utc(item.at),
        band=item.band,
        factual=item.factual,
        independent=item.independent,
    )


@dataclass
class Layout:
    """The topics, objectives and live question counts of a subject."""

    subject: Subject
    topics: list[Topic]  # active, by ordinal and id
    objectives: dict[int, list[LearningObjective]]  # by topic id
    objective_topic: dict[int, int]  # objective id -> topic id (active topics only)
    by_objective: dict[int, int]  # live questions per objective
    loose: dict[int, int]  # live questions without an objective, per family topic


def load_layout(
    session: Session, subject_id: int, layer: str = "materials", chapter_id: int | None = None
) -> Layout:
    """`chapter_id` keeps only that chapter's topics; answers elsewhere then own no line."""
    subject = session.get(Subject, subject_id)
    if subject is None:
        raise HTTPException(status_code=404, detail=SUBJECT_NOT_FOUND)
    shown = (Topic.subject_id == subject_id) & (Topic.status == "active")
    if chapter_id is not None:
        shown = shown & (Topic.chapter_id == chapter_id)
    topics = list(session.scalars(select(Topic).where(shown).order_by(Topic.ordinal, Topic.id)))
    objectives: dict[int, list[LearningObjective]] = defaultdict(list)
    for objective in session.scalars(
        select(LearningObjective)
        .join(Topic, Topic.id == LearningObjective.topic_id)
        .where(shown)
        .order_by(LearningObjective.ordinal, LearningObjective.id)
    ):
        objectives[objective.topic_id].append(objective)
    latest = (
        select(QuestionVersion.family_id, func.max(QuestionVersion.version).label("version"))
        .group_by(QuestionVersion.family_id)
        .subquery()
    )
    by_objective: dict[int, int] = defaultdict(int)
    loose: dict[int, int] = defaultdict(int)
    live = (QuestionFamily.subject_id == subject_id) & QuestionVersion.status.in_(LIVE_STATUSES)
    if layer != "all":
        live = live & (QuestionVersion.source_policy == layer)
    for objective_id, topic_id, count in session.execute(
        select(QuestionVersion.objective_id, QuestionFamily.primary_topic_id, func.count())
        .join(QuestionFamily, QuestionFamily.id == QuestionVersion.family_id)
        .join(
            latest,
            (latest.c.family_id == QuestionVersion.family_id)
            & (latest.c.version == QuestionVersion.version),
        )
        .where(live)
        .group_by(QuestionVersion.objective_id, QuestionFamily.primary_topic_id)
    ):
        if objective_id is not None:
            by_objective[objective_id] += count
        elif topic_id is not None:
            loose[topic_id] += count
    return Layout(
        subject=subject,
        topics=topics,
        objectives=objectives,
        objective_topic={o.id: o.topic_id for items in objectives.values() for o in items},
        by_objective=by_objective,
        loose=loose,
    )


def line_key(row: AttemptRow, layout: Layout) -> LineKey | None:
    """The line an attempt is evidence for, or None when no shown line owns it."""
    if row.objective_id is not None:
        topic_id = layout.objective_topic.get(row.objective_id)
        return None if topic_id is None else (topic_id, row.objective_id)
    if row.family_topic_id is None or row.question_status not in LIVE_STATUSES:
        return None
    return (row.family_topic_id, None)


def evidence_by_line(rows: list[AttemptRow], layout: Layout) -> dict[LineKey, list[Evidence]]:
    grouped: dict[LineKey, list[Evidence]] = defaultdict(list)
    for row in rows:
        key = line_key(row, layout)
        item = _evidence(row)
        if key is not None and item is not None:
            grouped[key].append(item)
    return grouped


def _line(
    objective: LearningObjective | None, evidence: list[Evidence], question_count: int
) -> ObjectiveCoverage:
    result = status_for(evidence)
    ordered = sorted(evidence, key=lambda e: (e.at, e.attempt_id))
    independent = [e for e in ordered if e.independent]
    prepared = [e.preparation_used_ms for e in independent if e.preparation_used_ms is not None]
    return ObjectiveCoverage(
        objective_id=objective.id if objective is not None else None,
        text=objective.text if objective is not None else None,
        status=result.status,
        assisted=result.assisted,
        answers=len(ordered),
        independent_answers=len(independent),
        last_practised_at=_utc(ordered[-1].at) if ordered else None,
        recent_factual=[e.factual for e in reversed(independent[-RECENT:])],
        deciding=evidence_out(result.deciding),
        median_preparation_used_ms=round(median(prepared[-RECENT:])) if prepared else None,
        question_count=question_count,
    )


def topic_lines(
    layout: Layout, topic: Topic, grouped: dict[LineKey, list[Evidence]]
) -> list[ObjectiveCoverage]:
    objectives = layout.objectives.get(topic.id, [])
    lines = [
        _line(o, grouped.get((topic.id, o.id), []), layout.by_objective.get(o.id, 0))
        for o in objectives
    ]
    loose = layout.loose.get(topic.id, 0)
    if not objectives or loose or grouped.get((topic.id, None)):
        lines.append(_line(None, grouped.get((topic.id, None), []), loose))
    return lines


def layer_lines(lines: list[ObjectiveCoverage], layer: str) -> list[ObjectiveCoverage]:
    """The expanded layer shows only lines with a live expanded question or expanded evidence;
    "all" shows every line, as "materials" does."""
    if layer in ("materials", "all"):
        return lines
    return [line for line in lines if line.question_count or line.answers]


def subject_coverage(
    session: Session, subject_id: int, layer: str = "materials", chapter_id: int | None = None
) -> SubjectCoverage:
    layout = load_layout(session, subject_id, layer, chapter_id)
    grouped = evidence_by_line(load_attempts(session, subject_id, layer=layer), layout)
    line_counts: dict[CoverageStatus, int] = dict.fromkeys(STATUS_ORDER, 0)
    topic_counts: dict[CoverageStatus, int] = dict.fromkeys(STATUS_ORDER, 0)
    topics = []
    for topic in layout.topics:
        lines = layer_lines(topic_lines(layout, topic, grouped), layer)
        if not lines:
            continue
        status = topic_status([line.status for line in lines])
        for line in lines:
            line_counts[line.status] += 1
        topic_counts[status] += 1
        topics.append(
            TopicCoverage(
                topic_id=topic.id,
                title=topic.title,
                status=status,
                objectives=lines,
                chapter_id=topic.chapter_id,
            )
        )
    return SubjectCoverage(
        subject_id=layout.subject.id,
        subject_name=layout.subject.name,
        line_total=sum(line_counts.values()),
        line_counts=line_counts,
        topic_counts=topic_counts,
        topics=topics,
        layer=layer,
        chapter_id=chapter_id,
    )


TOPIC_NOT_FOUND = "Topic not found."
HISTORY_LIMIT = 50
SOURCE_LIMIT = 20
EXCERPT_CHARS = 240


def _excerpt(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= EXCERPT_CHARS else text[: EXCERPT_CHARS - 1].rstrip() + "…"


def _history_entry(row: AttemptRow) -> HistoryEntry:
    band = None
    factual = None
    language = None
    if row.outcome in ("dont_know", "skipped"):
        status = "ready"
        band, factual = "weak", 0.0
    elif row.outcome == "interrupted":
        status = "interrupted"
    elif row.outcome == "given_up":
        status = "not_assessed"
    elif row.outcome == "technical_failure" or row.processing_status == "failed":
        status = "failed"
    elif row.processing_status == "ready" and row.processing == "ready":
        status = "ready"
        assessment = row.assessment or {}
        if assessment.get("status") == "assessed":
            scores = assessment.get("scores") or {}
            band = assessment.get("band")
            factual = scores.get("factual")
            language = scores.get("language")
    else:
        status = "processing"
    prep = (row.metrics or {}).get("preparation_used_ms")
    return HistoryEntry(
        attempt_id=row.attempt_id,
        round_id=row.round_id,
        at=row.at,
        question_text=row.question_text,
        style=row.style,
        difficulty=row.difficulty,
        objective_id=row.objective_id,
        attempt_kind=row.kind,
        independent=row.independent,
        outcome=row.outcome,
        status=status,
        band=band,
        factual=factual,
        language=language,
        preparation_used_ms=prep if isinstance(prep, int) and status == "ready" else None,
    )


def topic_detail(session: Session, topic_id: int, layer: str = "materials") -> TopicDetail:
    subject_id = session.scalar(
        select(Topic.subject_id).where(Topic.id == topic_id, Topic.status == "active")
    )
    if subject_id is None:
        raise HTTPException(status_code=404, detail=TOPIC_NOT_FOUND)
    layout = load_layout(session, subject_id, layer)
    topic = next(t for t in layout.topics if t.id == topic_id)
    objective_ids = [o.id for o in layout.objectives.get(topic_id, [])]
    rows = load_attempts(session, subject_id, objective_ids, topic_id, layer)
    lines = layer_lines(topic_lines(layout, topic, evidence_by_line(rows, layout)), layer)
    newest = sorted(rows, key=lambda r: (r.at, r.attempt_id), reverse=True)[:HISTORY_LIMIT]
    terms = list(
        session.scalars(select(Term.term).where(Term.topic_id == topic_id).order_by(Term.term))
    )
    usable = (TopicPassage.topic_id == topic_id) & (SourcePassage.excluded.is_(False))
    passage_count = session.scalar(
        select(func.count())
        .select_from(TopicPassage)
        .join(SourcePassage, SourcePassage.id == TopicPassage.passage_id)
        .where(usable)
    )
    sources = [
        TopicSource(
            passage_id=passage.id,
            document_id=document_id,
            document_title=title,
            document_version=version,
            locator=passage.locator,
            heading=passage.heading,
            excerpt=_excerpt(passage.text),
        )
        for passage, document_id, title, version in session.execute(
            select(SourcePassage, Document.id, Document.title, DocumentVersion.version)
            .join(TopicPassage, TopicPassage.passage_id == SourcePassage.id)
            .join(DocumentVersion, DocumentVersion.id == SourcePassage.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(usable)
            .order_by(Document.title, DocumentVersion.version, SourcePassage.ordinal)
            .limit(SOURCE_LIMIT)
        )
    ]
    return TopicDetail(
        topic_id=topic.id,
        subject_id=subject_id,
        subject_name=layout.subject.name,
        title=topic.title,
        description=topic.description,
        status=topic_status([line.status for line in lines]),
        objectives=lines,
        question_count=sum(line.question_count for line in lines),
        terms=terms,
        passage_count=passage_count or 0,
        history=[_history_entry(row) for row in newest],
        sources=sources,
        layer=layer,
    )
