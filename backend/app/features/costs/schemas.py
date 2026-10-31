from typing import Literal

from pydantic import BaseModel

Basis = Literal["typical", "measured"]
Group = Literal["operation", "round", "subject", "month"]


class MonthState(BaseModel):
    month: str  # YYYY-MM, this computer's time zone
    ceiling_usd: float  # 0: no ceiling
    spent_usd: float
    reserved_usd: float  # requests in flight
    remaining_usd: float | None  # None without a ceiling


class EstimatePart(BaseModel):
    operation: str
    usd: float


class RoundEstimate(BaseModel):
    low_usd: float
    high_usd: float
    transcription_minutes: float
    basis: Basis
    parts: list[EstimatePart]
    voice_provider: str
    month: MonthState
    fits: bool


class JobEstimate(BaseModel):
    kind: str
    usd: float
    questions: int = 0
    sections: int = 0
    minutes: int = 0
    provider: str
    basis: Basis
    month: MonthState
    fits: bool


class UsageRow(BaseModel):
    key: str
    label: str | None = None  # the Polish subject name for group=subject, else None
    provider: str | None = None
    model: str | None = None
    price_known: bool = True
    requests: int
    blocked: int
    input_tokens: int
    output_tokens: int
    audio_minutes: float
    characters: int
    estimated_usd: float | None
    reported_usd: float | None


class UsageTable(BaseModel):
    month: str
    group: Group
    rows: list[UsageRow]


class MonthSummary(BaseModel):
    month: MonthState
    rounds: int
    per_round_usd: float | None
    library_usd: float
    voice_usd: float
    voice_provider: str
    blocked: int
    months: list[str]  # months with usage, newest first


class PriceOut(BaseModel):
    provider: str
    model: str
    role: str
    input_per_m: float
    output_per_m: float
    audio_per_min: float
    chars_per_m: float
    checked: str
