from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

QuestionStyle = Literal["short", "open"]
Difficulty = Literal["recall", "apply", "analysis"]
SourcePolicy = Literal["materials", "expanded", "outside"]
Basis = Literal["source", "expanded", "mixed"]


# The rubric models are also the AI output schema, so they carry no range constraints: a bad
# weight or point count must reject one question in validation, not fail the whole batch.
class EssentialPoint(BaseModel):
    id: str  # "p1", "p2", … stable within a rubric
    text: str  # Polish
    weight: float  # > 0; rubric weights normalized to sum 1.0 by code
    alternatives: list[str] = Field(default_factory=list)
    passage_ids: list[int]  # source_passage ids; ≥ 1 under source policy "materials"
    basis: Basis = "source"  # "expanded" points carry no passage ids


class EnrichmentPoint(BaseModel):
    text: str
    passage_ids: list[int] = Field(default_factory=list)


class SourceIssue(BaseModel):
    kind: Literal["contradiction", "unclear", "insufficient"]
    passage_ids: list[int] = Field(default_factory=list)
    note: str  # Polish


class Rubric(BaseModel):
    essential_points: list[EssentialPoint]  # 2–8
    enrichment_points: list[EnrichmentPoint] = Field(default_factory=list)
    misconceptions: list[str] = Field(default_factory=list)
    major_error_rules: list[str] = Field(default_factory=list)
    model_answer: str  # concise, Polish
    expanded_explanation: str | None = None
    beyond_material: list[str] = Field(default_factory=list)  # model-answer details, Polish
    source_issues: list[SourceIssue] = Field(default_factory=list)


class PassageRef(BaseModel):
    id: int
    locator: str
    heading: str | None
    text: str


class QuestionOut(BaseModel):
    """No rubric: safe for exam screens."""

    version_id: int
    family_id: int
    version: int
    text: str
    style: QuestionStyle
    difficulty: Difficulty
    primary_topic_id: int | None
    objective_id: int | None
    status: str
    suggested_prep_s: int
    suggested_answer_s: int
    source_policy: str


class SourceChange(BaseModel):
    """A cited passage that changed (or was removed) in a newer version of its document."""

    document_id: int
    document_title: str
    old_version: int
    new_version: int
    old: PassageRef
    new: PassageRef | None  # None: removed


class QuestionWithRubric(QuestionOut):
    rubric: Rubric
    passages: list[PassageRef]
    review_note: str | None = None
    basis: Basis = "source"
    source_changes: list[SourceChange] = Field(default_factory=list)  # GET /questions/{id} only
    origin: str = "ai"  # "ai" | "user" | "kept", from the provenance


class GenerateIn(BaseModel):
    subject_id: int
    topic_ids: list[int] | None = None
    per_topic: int = Field(ge=1, le=10)
    style: Literal["short", "open", "mixed"] = "mixed"
    difficulty: Literal["recall", "apply", "analysis", "mixed"] = "mixed"
    source_policy: SourcePolicy = "materials"
    chapter_id: int | None = None  # only this chapter's topics
    document_version_id: int | None = None  # per book: topics with material from this version


class QuestionEdit(BaseModel):
    text: str | None = None
    style: QuestionStyle | None = None
    difficulty: Difficulty | None = None
    rubric: Rubric | None = None


Verdict = Literal["ok", "weak", "bad"]


class ReviewIn(BaseModel):
    verdict: Verdict
    note: str | None = Field(None, max_length=1000)


class ReviewOut(BaseModel):
    question_version_id: int
    verdict: Verdict
    note: str | None
    updated_at: datetime
