from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.db.legacy import DB_FILE, migrate_legacy_db

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AOC_",
        env_file_encoding="utf-8",
        extra="ignore",
        # An empty line copied from env-example.txt ("AOC_DATA_DIR=") means "use the default".
        env_ignore_empty=True,
    )

    data_dir: Path = REPO_ROOT / "data"
    database_url: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    azure_speech_key: SecretStr | None = None
    azure_speech_region: str | None = None
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"
    transcription_provider: Literal["openai"] = "openai"
    transcription_model: str = "gpt-transcribe"
    generation_provider: Literal["openai", "anthropic"] = "openai"
    generation_model: str = "gpt-6.1-sol"
    assessment_provider: Literal["openai", "anthropic"] = "openai"
    assessment_model: str = "gpt-6.1-sol"
    anthropic_default_model: str = "claude-sonnet-5-5"
    anthropic_fallbacks: bool = True
    voice_provider: Literal["piper", "azure_speech", "browser", "openai"] = "piper"
    voice_name: str = "pl_PL-bass-high"
    voice_rate: float = Field(default=1.0, ge=0.5, le=1.5)
    azure_voice_default: str = "pl-PL-ZofiaNeural"
    openai_voice_model: str = "gpt-realtime-2.1-mini"
    openai_voice_default: str = "marin"
    voices_dir: Path | None = None
    # Developer file (backend/.env); None = not read (tests). Settings saved in the browser
    # go to the user file in the data dir instead, which survives an update of the app folder.
    base_env_file: Path | None = APP_DIR.parent / ".env"
    env_file_path: Path | None = None
    factual_band_bounds: tuple[int, int, int] = (30, 60, 80)

    @field_validator("factual_band_bounds")
    @classmethod
    def _bands_increase(cls, value: tuple[int, int, int]) -> tuple[int, int, int]:
        if not (1 <= value[0] < value[1] < value[2] <= 99):
            raise ValueError("Band bounds must increase and lie within 1–99.")
        return value

    # Factual score split: correctness and completeness percentages of the
    # composite; together always 80, language keeps 20.
    factual_weights: tuple[int, int] = (30, 50)

    @field_validator("factual_weights")
    @classmethod
    def _weights_sum(cls, value: tuple[int, int]) -> tuple[int, int]:
        if not (10 <= value[0] <= 70 and 10 <= value[1] <= 70 and sum(value) == 80):
            raise ValueError(
                "Correctness and completeness weights must each be 10–70 and sum to 80."
            )
        return value

    # Defaults for new rounds (quick practice and the round setup page).
    round_question_count: int = Field(default=10, ge=1, le=30)
    round_prep_seconds: int = Field(default=30, ge=0, le=600)
    round_answer_seconds: int = Field(default=120, ge=5, le=600)

    # How long answer recordings are kept: forever, until the answer is assessed, or N days.
    audio_retention: Literal["forever", "assessed", "30d", "90d"] = "forever"

    # Automatic clean-up: the newest N pre-restore backups are kept and exports are deleted
    # after N days; 0 keeps all. Your own backups are never deleted automatically.
    pre_restore_keep: int = Field(default=5, ge=0, le=100)
    export_keep_days: int = Field(default=30, ge=0, le=3650)

    # Monthly ceiling for this app's AI usage in USD; 0 = no ceiling.
    spending_ceiling_usd: float = Field(default=0.0, ge=0, le=10000)

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        migrate_legacy_db(self.data_dir)
        return f"sqlite:///{(self.data_dir / DB_FILE).as_posix()}"

    @property
    def user_env_file(self) -> Path:
        return self.env_file_path or self.data_dir / "settings.env"

    @property
    def effective_voices_dir(self) -> Path:
        return self.voices_dir or self.data_dir / "voices"


def load_settings(**overrides: Any) -> Settings:
    """Settings from env vars > the user file in the data dir > backend/.env > defaults."""
    base = Settings(_env_file=None, **overrides).base_env_file
    first = Settings(_env_file=base if base and base.is_file() else None, **overrides)
    files = [path for path in (first.base_env_file, first.user_env_file) if path and path.is_file()]
    # Explicit values keep the user file from moving the data dir or the files themselves.
    pinned = {
        "data_dir": first.data_dir,
        "base_env_file": first.base_env_file,
        "env_file_path": first.env_file_path,
    }
    return Settings(_env_file=files or None, **{**overrides, **pinned})


@lru_cache
def get_settings() -> Settings:
    return load_settings()
