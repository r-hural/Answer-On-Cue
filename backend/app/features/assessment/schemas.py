from typing import Literal

from pydantic import BaseModel, Field

from app.features.delivery.schemas import AnswerMetrics
from app.features.providers.types import Provenance
from app.features.questions.schemas import QuestionWithRubric

PointStatus = Literal["covered", "partial", "missing", "wrong"]
RegisterKey = Literal["grammar", "vocabulary", "formality", "terminology", "structure", "precision"]
REGISTER_KEYS: tuple[RegisterKey, ...] = (
    "grammar",
    "vocabulary",
    "formality",
    "terminology",
    "structure",
    "precision",
)
AttemptKind = Literal["initial", "immediate", "retry", "followup", "extension"]


class PointJudgement(BaseModel):
    point_id: str  # rubric EssentialPoint.id
    status: PointStatus
    evidence: str | None = None  # exact quote from the transcript, None when missing
    note: str  # Polish, one sentence
    disputed: bool = False  # the sources disagree on this point: left out of completeness


class SourceConflict(BaseModel):
    point_id: str | None = None  # rubric EssentialPoint.id, or None for the whole question
    passage_ids: list[int] = Field(default_factory=list)  # the passages that disagree
    note: str  # Polish


class FactualMistake(BaseModel):
    quote: str  # learner's words
    correction: str  # Polish
    major: bool  # matches a rubric major_error_rules entry
    rule_index: int | None = None  # index into rubric.major_error_rules


class OutsideMaterial(BaseModel):
    quote: str
    judgement: Literal["valid", "doubtful", "wrong"]  # valid extra knowledge is never penalised
    note: str


class RegisterJudgement(BaseModel):
    summary: str  # Polish, short
    quotes: list[str] = Field(default_factory=list)  # evidence from the transcript


# Fixed fields instead of a dict keyed by RegisterKey: a strict structured-output schema accepts no
# open-ended objects, and "register" itself would shadow BaseModel.register.
class RegisterSet(BaseModel):
    grammar: RegisterJudgement
    vocabulary: RegisterJudgement
    formality: RegisterJudgement
    terminology: RegisterJudgement
    structure: RegisterJudgement
    precision: RegisterJudgement

    def by_key(self) -> dict[RegisterKey, RegisterJudgement]:
        return {key: getattr(self, key) for key in REGISTER_KEYS}


class LanguageIssue(BaseModel):
    dimension: RegisterKey
    quote: str
    suggestion: str  # Polish
    explanation: str  # Polish


class TranscriptionDoubt(BaseModel):
    quote: str
    likely_meant: str | None = None
    reason: str


# The AI output schema (task "assessment.assess"). It carries no completeness number: completeness,
# factual and total are always computed in code from the point judgements.
class AiAssessment(BaseModel):
    correctness: int = Field(ge=0, le=100)
    correctness_evidence: list[str]
    points: list[PointJudgement]
    factual_mistakes: list[FactualMistake] = Field(default_factory=list)
    enrichment_mentioned: list[str] = Field(default_factory=list)
    outside_material: list[OutsideMaterial] = Field(default_factory=list)
    language: int | None = Field(default=None, ge=0, le=100)
    language_register: RegisterSet  # grammar, vocabulary and the four exam-register dimensions
    language_issues: list[LanguageIssue] = Field(default_factory=list)
    better_version: str  # learner's meaning, formal exam Polish
    improvements: list[str]  # 1–2 actionable items
    fluency: int | None = Field(default=None, ge=0, le=100)
    fluency_coverage: Literal["full", "partial", "none"]
    fluency_confidence: Literal["low", "medium", "high"]
    fluency_feedback: str
    hesitation_examples: list[str] = Field(default_factory=list)
    transcription_doubts: list[TranscriptionDoubt] = Field(default_factory=list)
    uncertainty_flags: list[str] = Field(default_factory=list)
    source_conflicts: list[SourceConflict] = Field(default_factory=list)


Band = Literal["weak", "needs_improvement", "good", "very_good"]
BAND_LABELS: dict[Band, str] = {
    "weak": "Weak",
    "needs_improvement": "Needs improvement",
    "good": "Good",
    "very_good": "Very good",
}
BandBounds = tuple[int, int, int]  # lower bounds of Needs improvement, Good, Very good
DEFAULT_BAND_BOUNDS: BandBounds = (30, 60, 80)  # mirrors Settings.factual_band_bounds
FactualWeights = tuple[int, int]  # % of correctness and completeness in total; sum 80
DEFAULT_FACTUAL_WEIGHTS: FactualWeights = (30, 50)  # mirrors Settings.factual_weights
ModelAnswerDisplay = Literal["auto", "one_click", "on_request"]


class Scores(BaseModel):
    """Unrounded component values; round only for display."""

    correctness: float
    completeness: float
    factual: float
    language: float | None
    total: float


class AssessmentInput(BaseModel):
    question: QuestionWithRubric
    transcript: str | None
    outcome: Literal["answered", "dont_know", "skipped"]
    attempt_kind: AttemptKind = "initial"
    interrupted: bool = False
    delivery: AnswerMetrics | None = None


class AssessmentResult(BaseModel):
    status: Literal["assessed", "no_answer", "no_speech"]
    scores: Scores | None
    band: Band | None
    model_answer_display: ModelAnswerDisplay | None
    ai: AiAssessment | None
    band_bounds: BandBounds
    factual_weights: FactualWeights
    prompt_version: str | None  # None when no AI call was made
    question_version_id: int
    provenance: Provenance | None
    attempts_used: int  # AI calls made: 0, 1 or 2


class AssessmentError(Exception):
    """Invalid AI output after the retry. The message is in English and safe to show."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        self.message = message
        self.errors = errors or []
        super().__init__(message)
