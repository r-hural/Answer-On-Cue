"""Read models for the feedback screen, the round summary and the history."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

from app.features.assessment.aggregate import RoundKnowledgeSummary
from app.features.assessment.schemas import Band, PointStatus
from app.features.attempts.schemas import AttemptResultOut, Failure
from app.features.delivery.schemas import RoundDeliverySummary
from app.features.questions.schemas import Basis
from app.features.rounds.schemas import RoundConfig

FollowUpReason = Literal[
    "no_answer", "main_unresolved", "generation_failed", "continued", "not_reached"
]


class PointOut(BaseModel):
    id: str
    text: str  # Polish rubric text
    status: PointStatus
    evidence: str | None
    note: str
    basis: Basis = "source"  # source policy: a point beyond the materials is labelled
    disputed: bool = False  # the sources disagree: not graded, shown in source_conflicts


class SourceOut(BaseModel):
    passage_id: int
    document_title: str
    locator: str
    heading: str | None
    excerpt: str  # ≤ 240 chars


class ConflictOut(BaseModel):
    """A point on which the sources disagree, with both passages."""

    point_id: str | None
    point_text: str | None  # Polish rubric text; None for the whole question
    note: str  # Polish
    sources: list[SourceOut]


class TranscriptReportIn(BaseModel):
    corrected_text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)
    ]


class TranscriptReportOut(BaseModel):
    attempt_id: int
    corrected_text: str
    created_at: datetime


class FollowUpBrief(BaseModel):
    """The main answer's follow-up, for its feedback box; scores kept apart."""

    asked: bool
    reason: FollowUpReason | None  # why it was not asked
    failure_message: str | None  # English, for generation_failed
    question_text: str | None  # Polish
    attempt_id: int | None
    outcome: str | None
    status: Literal["ready", "failed", "processing"] | None
    band: Band | None
    factual: float | None
    note: str | None  # the first two point notes, Polish


class FollowUpSeries(BaseModel):
    asked: int
    not_asked: int
    unresolved: int
    good_or_better: int
    provisional: bool
    knowledge: RoundKnowledgeSummary
    processing: int = 0  # asked follow-ups whose answer is still processing (clients poll)


class ExtensionSeries(BaseModel):
    """The adaptive extension's own scores; the round's primary score never sees them."""

    round_id: int
    status: str
    asked: int
    total: int
    good_or_better: int
    provisional: bool
    withheld: bool
    knowledge: RoundKnowledgeSummary | None


class AttemptReport(BaseModel):
    attempt_id: int
    round_id: int
    subject_id: int
    subject_name: str
    ordinal: int  # 1-based
    total: int
    feedback_timing: str
    round_status: str
    question_text: str
    result: AttemptResultOut  # exactly what GET /attempts/{id}/result returns
    points: list[PointOut]
    key_points: list[str]
    model_answer: str | None
    expanded_explanation: str | None
    model_answer_display: str | None
    sources: list[SourceOut]
    transcript_report: TranscriptReportOut | None
    role: Literal["main", "followup"] = "main"
    parent_attempt_id: int | None = None  # a follow-up's main attempt
    follow_up: FollowUpBrief | None = None  # main answers of rounds with follow-ups on
    round_type: str = "standard"
    source_policy: str = "materials"  # the question's policy
    question_basis: Basis = "source"
    beyond_material: list[str] = []  # Polish; only with the model answer
    source_conflicts: list[ConflictOut] = []  # only when the points are shown
    attempt_kind: str = "initial"
    original: "AnswerBrief | None" = None  # a repeat's or retry's original answer
    again_attempt_id: int | None = None  # an original's repeat (Answer again)


class ItemRow(BaseModel):
    ordinal: int  # 1-based
    item_id: int
    attempt_id: int | None
    question_text: str
    outcome: str
    status: Literal["ready", "failed", "processing", "not_asked"]
    band: Band | None
    factual: float | None
    total: float | None
    preparation_used_ms: int | None
    speech_start_latency_ms: int | None
    auto_start: bool
    fluency: int | None
    restarted: bool
    failure: Failure | None
    again: "AnswerBrief | None" = None  # the item's repeat (Answer again), apart from the score


class RoundReport(BaseModel):
    round_id: int
    subject_id: int
    subject_name: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    config: RoundConfig
    withheld: bool
    provisional: bool
    asked: int
    total: int
    knowledge: RoundKnowledgeSummary | None
    delivery: RoundDeliverySummary | None
    items: list[ItemRow]
    pause_count: int
    paused_ms: int
    restarted_ordinals: list[int]
    follow_ups: FollowUpSeries | None = None  # None when follow-ups are off or scores withheld
    parent_round_id: int | None = None  # an adaptive extension's round
    selection_counts: dict[str, int] | None = None  # adaptive rounds only: why items were chosen
    extension: ExtensionSeries | None = None  # the round's adaptive extension, once started
    repeats: "RepeatSeries | None" = None  # the round's Answer again repeats
    retry: "RetrySeries | None" = None  # the round's retry round, once started
    retry_comparison: "RetryComparison | None" = None  # a retry round against its original
    ai_cost_usd: float | None = None  # the round's usage; None when it has none


class HistoryRow(BaseModel):
    round_id: int
    subject_id: int
    subject_name: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    feedback_timing: str
    total: int
    asked: int
    primary_score: float | None
    provisional: bool
    band_distribution: dict[Band, int]
    median_preparation_used_ms: int | None
    mean_fluency: float | None
    withheld: bool
    round_type: str = "standard"
    source_policy: str = "materials"  # the round's comparison group
    parent_round_id: int | None = None  # a retry round's original round
    parent_started_at: datetime | None = None
    # the round's usage; None when it has none. Before the chapter fields: the chapters
    # tests pin those as the row's last two keys.
    ai_cost_usd: float | None = None
    chapter_id: int | None = None  # config.scope.chapter_id
    chapter_name: str | None = None


class AnswerBrief(BaseModel):
    """One answer in a comparison: an original next to its repeat or retry."""

    attempt_id: int | None
    outcome: str
    status: Literal["ready", "failed", "processing", "not_asked"]
    band: Band | None
    factual: float | None
    total: float | None
    preparation_used_ms: int | None
    long_pause_count: int | None
    fluency: int | None


class SeriesRow(BaseModel):
    ordinal: int  # 1-based, in the round shown
    item_id: int
    question_text: str
    original: AnswerBrief
    again: AnswerBrief  # the repeat (Answer again) or the retry round's answer
    factual_change: float | None  # again − original, when both have a factual score
    below_good: bool  # the second answer is still below Good


class RepeatSeries(BaseModel):
    """The round's Answer again repeats: assisted, never part of the round score."""

    asked: int
    good_or_better: int
    improved: int
    rows: list[SeriesRow]
    knowledge: RoundKnowledgeSummary
    delivery: RoundDeliverySummary | None


class RetrySeries(BaseModel):
    """The round's retry round, on the original's summary; its own score, never this round's."""

    round_id: int
    status: str
    started_at: datetime
    asked: int
    total: int
    good_or_better: int
    still_below: int
    provisional: bool
    withheld: bool
    knowledge: RoundKnowledgeSummary | None


class RetryComparison(BaseModel):
    """A retry round against its original round's answers to the same questions."""

    parent_round_id: int
    parent_started_at: datetime
    paired: int  # rows where both answers have a factual score
    original_mean_total: float | None
    retry_mean_total: float | None
    original_mean_factual: float | None
    retry_mean_factual: float | None
    original_median_preparation_ms: int | None
    retry_median_preparation_ms: int | None
    rows: list[SeriesRow]
    still_below: list[int]  # ordinals


AttemptReport.model_rebuild()
ItemRow.model_rebuild()
RoundReport.model_rebuild()
