from typing import Literal

from pydantic import BaseModel

from app.features.assessment.schemas import AssessmentResult
from app.features.delivery.schemas import AnswerMetrics, ClientEvents

Status = Literal["processing", "ready", "failed"]


class AttemptEventsIn(ClientEvents):
    """The answer's client timings (performance.now() ms on one page clock)."""

    listening_available: bool = True


class AttemptEventsOut(BaseModel):
    attempt_id: int
    stored: bool


class AttemptRoundOut(BaseModel):
    attempt_id: int
    round_id: int


class AttemptAudioOut(BaseModel):
    attempt_id: int
    recording_id: int
    duration_ms: int


class RecordingRef(BaseModel):
    id: int
    duration_ms: int


class Failure(BaseModel):
    kind: str
    message: str  # English, safe to show
    retryable: bool


class AttemptResultOut(BaseModel):
    """A pinned contract between the attempts API and its clients: no field may be added."""

    attempt_id: int
    outcome: str
    status: Status
    stage: str
    recording: RecordingRef | None
    failure: Failure | None
    withheld: bool
    transcript: str | None
    metrics: AnswerMetrics | None
    assessment: AssessmentResult | None
