from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Series = Literal["main", "followup", "immediate", "retry", "extension"]
Change = Literal["band_table", "model", "analysis"]


class TrendFilters(BaseModel):
    subject_id: int
    source_policy: Literal["materials", "expanded", "all"] = "materials"
    series: Series = "main"
    style: Literal["short", "open"] | None = None
    difficulty: Literal["recall", "apply", "analysis"] | None = None
    include_interrupted: bool = False
    include_assisted: bool = False


class TrendPoint(BaseModel):
    """One finished or abandoned round, for one series. `None` is unavailable, never 0."""

    round_id: int
    started_at: datetime  # timezone-aware UTC
    status: Literal["finished", "abandoned"]
    round_type: str
    feedback_timing: str
    answers: int
    unresolved: int
    assisted: int
    restarted: int
    provisional: bool
    primary: float | None
    factual: float | None
    language: float | None
    language_assessable: int
    speed_answers: int
    median_preparation_used_ms: int | None
    median_speech_start_latency_ms: int | None
    median_total_response_latency_ms: int | None
    median_total_response_latency_successful_ms: int | None
    long_pauses_per_min: float | None
    silence_ratio: float | None
    filler_per_min: float | None
    filler_reliable: int
    fluency: float | None
    fluency_assessable: int
    band_bounds: tuple[int, int, int]
    changes: list[Change]


class TrendsOut(BaseModel):
    subject_id: int
    subject_name: str
    filters: TrendFilters
    band_bounds: tuple[int, int, int]  # the current settings, for the washes
    points: list[TrendPoint]  # oldest first, at most TRENDS_LIMIT
