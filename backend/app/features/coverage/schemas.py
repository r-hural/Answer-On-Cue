from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.features.assessment.schemas import Band
from app.features.coverage.rules import CoverageStatus


class EvidenceOut(BaseModel):
    """A read model of one counted answer; the "coverage evidence" is never a stored row."""

    attempt_id: int
    round_id: int
    at: datetime
    band: Band
    factual: float
    independent: bool


class ObjectiveCoverage(BaseModel):
    objective_id: int | None  # None: the line of questions on the whole topic
    text: str | None
    status: CoverageStatus
    assisted: bool
    answers: int
    independent_answers: int
    last_practised_at: datetime | None
    recent_factual: list[float]  # up to 3 independent scores, newest first
    deciding: EvidenceOut | None
    median_preparation_used_ms: int | None
    question_count: int


class TopicCoverage(BaseModel):
    topic_id: int
    title: str
    status: CoverageStatus
    objectives: list[ObjectiveCoverage]
    chapter_id: int | None = None


class SubjectCoverage(BaseModel):
    subject_id: int
    subject_name: str
    line_total: int
    line_counts: dict[CoverageStatus, int]
    topic_counts: dict[CoverageStatus, int]
    topics: list[TopicCoverage]
    layer: str = "materials"  # source policy of the questions counted
    chapter_id: int | None = None  # the chapter filter applied, None for the whole subject


class HistoryEntry(BaseModel):
    attempt_id: int
    round_id: int
    at: datetime
    question_text: str
    style: str
    difficulty: str
    objective_id: int | None
    attempt_kind: str
    independent: bool
    outcome: str
    status: Literal["ready", "failed", "processing", "interrupted", "not_assessed"]
    band: Band | None
    factual: float | None
    language: float | None
    preparation_used_ms: int | None


class TopicSource(BaseModel):
    passage_id: int
    document_id: int
    document_title: str
    document_version: int
    locator: str
    heading: str | None
    excerpt: str


class TopicDetail(BaseModel):
    topic_id: int
    subject_id: int
    subject_name: str
    title: str
    description: str | None
    status: CoverageStatus
    objectives: list[ObjectiveCoverage]
    question_count: int
    terms: list[str]
    passage_count: int
    history: list[HistoryEntry]
    sources: list[TopicSource]
    layer: str = "materials"
