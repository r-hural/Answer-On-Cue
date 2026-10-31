from typing import Literal

from pydantic import BaseModel

from app.features.recording.schemas import VadResult  # noqa: F401  (re-exported for callers)


class Thresholds(BaseModel):
    speech_start_flag_ms: int = 3000
    long_pause_ms: int = 2000
    very_long_pause_ms: int = 5000


StartReason = Literal["auto", "button", "timer"]


class ClientEvents(BaseModel):
    question_ready_at: float
    question_shown_at: float
    playback_ms: int = 0
    prep_end_at: float
    start_reason: StartReason
    capture_start_at: float
    stopped_at: float
    pre_roll_ms: int
    interrupted: bool = False


class PauseMarker(BaseModel):
    start_ms: int
    end_ms: int
    kind: Literal["long", "very_long"]


class AnswerMetrics(BaseModel):
    preparation_used_ms: int
    speech_start_latency_ms: int | None
    total_response_latency_ms: int | None
    answer_duration_ms: int
    first_to_last_speech_ms: int | None
    speech_ms: int
    leading_silence_ms: int | None
    trailing_silence_ms: int | None
    internal_silence_ms: int | None
    silence_ratio: float | None
    longest_internal_silence_ms: int | None
    long_pause_count: int | None
    very_long_pause_count: int | None
    long_pauses_per_min: float | None
    words: int | None
    words_per_minute: float | None
    filler_count: int | None
    filler_per_min: float | None
    filler_examples: list[str]
    fillers_lower_bound: bool = True
    pauses: list[PauseMarker]
    flags: list[Literal["slow_start"]]
    unavailable: dict[str, str]
    approximate: bool = True
    interrupted: bool
    detector: str
    thresholds: Thresholds


class AttemptForAggregate(BaseModel):
    metrics: AnswerMetrics
    attempt_kind: Literal["initial", "immediate", "retry", "followup", "extension"]
    factual_score: float | None


class RoundDeliverySummary(BaseModel):
    count_included: int
    count_excluded_interrupted: int
    count_excluded_other_kind: int
    median_preparation_used_ms: int | None
    median_speech_start_latency_ms: int | None
    median_total_response_latency_ms: int | None
    median_total_response_latency_successful_ms: int | None
    target_attainment: dict[str, tuple[int, int]]
    median_longest_pause_ms: int | None
    long_pauses_per_speaking_min: float | None
    aggregate_silence_ratio: float | None
    median_words_per_minute: float | None
    filler_per_min: float | None
