from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FeedbackTiming = Literal["practice", "exam"]
StyleChoice = Literal["short", "open", "mixed"]
DifficultyChoice = Literal["recall", "apply", "analysis", "mixed"]  # mixed = any difficulty
AudioMode = Literal["off", "premium", "browser"]

NEED_TOPICS = "Select at least one topic."
NEED_DOCUMENTS = "Select at least one document."
NEED_CHAPTER = "Select a chapter."
WHOLE_SUBJECT_CHAPTER = "A whole-subject round has no chapter."
NEED_START_REASON = "Specify how the answer was started."
EXTENSION_FROM_SUMMARY = "An adaptive extension starts from a round summary."
REPEAT_NEEDS_PRACTICE = (
    "Answer again needs practice feedback: switch to practice feedback or turn Answer again off."
)
RETRY_FROM_SUMMARY = "A retry round starts from a round summary."
RETRY_NOT_NOW = "A retry round is not available for this round now."

RoundType = Literal["standard", "adaptive", "extension", "retry"]
# Practice pools: "materials" only, or "all" questions; "expanded" is legacy (stored snapshots
# and their extension and retry children).
RoundSourcePolicy = Literal["materials", "expanded", "all"]
Selection = Literal["weak", "unpractised", "review", "balanced"]
ExtensionStatus = Literal[
    "off",
    "started",
    "ended_early",
    "not_finished",
    "waiting",
    "no_weak",
    "ready",
    "generating",
    "no_questions",
]


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["subject", "topics", "documents", "chapter"]
    topic_ids: list[int] = Field(default_factory=list)  # required (≥1) for kind="topics"
    document_ids: list[int] = Field(default_factory=list)  # required (≥1) for kind="documents"
    # required for kind="chapter"; for topics/documents the chapter the round was set up in
    chapter_id: int | None = None

    @model_validator(mode="after")
    def _ids_for_kind(self) -> "Scope":
        if self.kind == "topics" and not self.topic_ids:
            raise ValueError(NEED_TOPICS)
        if self.kind == "documents" and not self.document_ids:
            raise ValueError(NEED_DOCUMENTS)
        if self.kind == "chapter" and self.chapter_id is None:
            raise ValueError(NEED_CHAPTER)
        if self.kind == "subject" and self.chapter_id is not None:
            raise ValueError(WHOLE_SUBJECT_CHAPTER)
        return self


class QuestionAudio(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: AudioMode = "off"
    provider: Literal["piper", "azure_speech", "openai"] | None = None  # snapshot for "premium"
    voice: str | None = None
    rate: float | None = None


class FollowUpConfig(BaseModel):
    """One personalised follow-up after a usable main answer, with its own timers."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False  # old rounds and clients: off
    prep_seconds: int = Field(15, ge=0, le=600)
    answer_seconds: int = Field(60, ge=5, le=600)


class ExtensionConfig(BaseModel):
    """End-of-round adaptive extension: N new questions on the round's weak topics."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False  # old rounds and clients: off
    question_count: int = Field(3, ge=1, le=10)


class RoundConfig(BaseModel):
    """The round's immutable snapshot (stored as round.config_json)."""

    model_config = ConfigDict(extra="forbid")

    round_type: RoundType = "standard"
    scope: Scope
    source_policy: RoundSourcePolicy = "materials"  # separate pools; "all": every policy
    feedback_timing: FeedbackTiming = "practice"
    question_count: int = Field(10, ge=1, le=30)
    style: StyleChoice = "mixed"
    difficulty: DifficultyChoice = "mixed"
    prep_seconds: int = Field(30, ge=0, le=600)  # zero allowed
    answer_seconds: int = Field(120, ge=5, le=600)
    question_audio: QuestionAudio = Field(default_factory=QuestionAudio)
    follow_ups: FollowUpConfig = Field(default_factory=FollowUpConfig)
    extension: ExtensionConfig = Field(default_factory=ExtensionConfig)
    immediate_repeat: bool = False  # "Answer again" after practice feedback; old rounds: off
    retry_round: bool = False  # the end-of-round retry round of answers below Good
    band_bounds: tuple[int, int, int] | None = None  # filled from settings; client value ignored
    factual_weights: tuple[int, int] | None = None  # filled from settings; client value ignored
    # The subject's content language, recorded at creation; rounds created before it have none.
    language: str | None = None


class RoundCreate(BaseModel):
    subject_id: int
    config: RoundConfig

    @model_validator(mode="after")
    def _no_direct_extension(self) -> "RoundCreate":
        if self.config.round_type == "extension":
            raise ValueError(EXTENSION_FROM_SUMMARY)
        if self.config.round_type == "retry":
            raise ValueError(RETRY_FROM_SUMMARY)
        if self.config.immediate_repeat and self.config.feedback_timing == "exam":
            # On the request only, so a stored snapshot always parses.
            raise ValueError(REPEAT_NEEDS_PRACTICE)
        return self


Phase = Literal["question", "preparation", "recording", "processing", "feedback", "summary"]
Part = Literal["main", "followup"]
Action = Literal[
    "question_ready",
    "start_answer",
    "stop",
    "dont_know",
    "skip",
    "mic_failure",
    "next",
    "pause",
    "resume",
    "abandon",
    "no_follow_up",
    "answer_again",
]
StartReason = Literal["auto", "button", "timer"]


class ActionIn(BaseModel):
    action: Action
    from_phase: Phase  # the phase the client believes it is in
    start_reason: StartReason | None = None  # required for start_answer
    attempt_id: int | None = (
        None  # the attempt the client acts on; a retry after it moved on is a duplicate
    )

    @model_validator(mode="after")
    def _reason_for_start(self) -> "ActionIn":
        if self.action == "start_answer" and self.start_reason is None:
            raise ValueError(NEED_START_REASON)
        return self


class AttemptOut(BaseModel):
    id: int
    attempt_kind: str
    outcome: str
    processing_status: str | None
    parent_attempt_id: int | None
    start_reason: str | None
    recording_id: int | None


class ItemOut(BaseModel):
    id: int
    ordinal: int
    role: str
    question_version_id: int
    status: Literal["pending", "current", "done"]
    latest_attempt: AttemptOut | None


class CurrentQuestion(BaseModel):
    """No rubric, no model answer: safe for the exam screen."""

    item_id: int
    question_version_id: int
    text: str
    style: str
    difficulty: str


class FollowUpFailure(BaseModel):
    kind: str
    message: str  # English, safe to show


class FollowUpState(BaseModel):
    """The current main item's follow-up generation; never its question, rubric or sources."""

    id: int
    status: Literal["generating", "ready", "failed", "omitted"]
    failure: FollowUpFailure | None
    retryable: bool


class PhaseClock(BaseModel):
    """The server's clock for the current phase, so a page that slept can find its deadlines."""

    server_now: datetime  # tz-aware UTC
    phase_started_at: datetime | None  # preparation: the attempt's start; recording: start_answer
    prep_deadline_at: datetime | None  # + the part's prep seconds (follow-up timers in "followup")
    answer_deadline_at: datetime | None  # + the part's answer seconds
    paused_at: datetime | None  # the open pause event, while paused


class RoundState(BaseModel):
    id: int
    subject_id: int
    status: str
    phase: Phase
    part: Part = "main"
    current_index: int
    total: int
    config: RoundConfig
    current: CurrentQuestion | None
    current_attempt: AttemptOut | None
    items: list[ItemOut]
    pause_count: int
    paused_ms: int
    follow_up: FollowUpState | None = None  # the current main item's, while the round is open
    can_answer_again: bool = False  # practice feedback may offer one repeat of the main question
    clock: PhaseClock | None = None


class RoundListItem(BaseModel):
    id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    total: int
    done: int
    feedback_timing: str
    round_type: str = "standard"


class WeakTopic(BaseModel):
    topic_id: int
    title: str  # Polish


class ExtensionOffer(BaseModel):
    """What a round's summary offers after the round (the end-of-round adaptive extension)."""

    round_id: int
    status: ExtensionStatus
    requested: int
    available: int  # new questions the extension would get now, at most `requested`
    topics: list[WeakTopic]  # the weak answers' topics, weakest first
    job_id: int | None  # the subject's live question generation
    extension_round_id: int | None
    extension_status: str | None
    needs_review: int = 0  # new questions on these topics waiting for the bank review


RetryStatus = Literal[
    "off",
    "started",
    "ended_early",
    "not_finished",
    "waiting",
    "extension_open",
    "none_below",
    "ready",
]


class RetryOffer(BaseModel):
    """The end-of-round retry round a summary offers: the answers below Good."""

    round_id: int
    status: RetryStatus
    count: int
    ordinals: list[int]  # 1-based, the questions it would ask
    unresolved: int  # technical failures and failed results: not in it (the recovery queue)
    retry_round_id: int | None
    retry_status: str | None


class RoundSummary(BaseModel):
    """A finished round's summary; scores come from the assessment and pipeline results."""

    round_id: int
    status: str
    provisional: bool
    outcomes: dict[str, int]  # final outcome per main item → count
    unresolved_attempt_ids: list[int]  # processing or technical_failure
    interrupted_attempt_ids: list[int]
    pause_count: int
    paused_ms: int


class AttemptContext(BaseModel):
    """What the attempt pipeline needs to process one attempt."""

    attempt_id: int
    round_id: int
    item_id: int
    subject_id: int
    question_version_id: int
    rubric_version_id: int
    attempt_kind: str
    interrupted: bool
    config: RoundConfig
