from typing import Literal

from pydantic import BaseModel, ConfigDict

Provider = Literal["openai", "anthropic"]
VoiceProvider = Literal["piper", "azure_speech", "browser", "openai"]


class Preferences(BaseModel):
    transcription_model: str
    generation_provider: Provider
    generation_model: str
    assessment_provider: Provider
    assessment_model: str
    voice_provider: VoiceProvider
    voice_name: str
    voice_rate: float
    factual_weights: tuple[int, int]
    factual_band_bounds: tuple[int, int, int]
    round_question_count: int
    round_prep_seconds: int
    round_answer_seconds: int
    spending_ceiling_usd: float = 0.0  # 0: no ceiling


class PreferencesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcription_model: str | None = None
    generation_provider: Provider | None = None
    generation_model: str | None = None
    assessment_provider: Provider | None = None
    assessment_model: str | None = None
    voice_provider: VoiceProvider | None = None
    voice_name: str | None = None
    voice_rate: float | None = None
    factual_weights: tuple[int, int] | None = None
    factual_band_bounds: tuple[int, int, int] | None = None
    round_question_count: int | None = None
    round_prep_seconds: int | None = None
    round_answer_seconds: int | None = None
    spending_ceiling_usd: float | None = None  # 0 clears the ceiling
