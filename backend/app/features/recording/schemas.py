from pydantic import BaseModel


class RecordingOut(BaseModel):
    id: int
    duration_ms: int
    purpose: str


class VadResult(BaseModel):
    engine: str
    speech_intervals: list[tuple[int, int]]
    speech_ms: int
    leading_silence_ms: int
    trailing_silence_ms: int
    duration_ms: int
