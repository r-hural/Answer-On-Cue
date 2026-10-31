from pydantic import BaseModel, ConfigDict, field_validator


class LanguageOut(BaseModel):
    code: str
    bcp47: str
    name_en: str
    name_native: str
    quotes: tuple[str, str]
    enabled: bool
    voice_provider: str | None
    voice_name: str | None
    subject_count: int


class LanguagePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    voice_provider: str | None = None
    voice_name: str | None = None

    @field_validator("enabled")
    @classmethod
    def _enabled_not_null(cls, value: bool | None) -> bool:
        # Null clears a voice field; "enabled" can be left out but not cleared.
        if value is None:
            raise ValueError("enabled must be true or false")
        return value
