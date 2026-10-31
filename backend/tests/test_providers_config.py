import pytest
from pydantic import ValidationError

from app.config import APP_DIR, Settings

ROLE_ENV = (
    "AOC_TRANSCRIPTION_PROVIDER",
    "AOC_TRANSCRIPTION_MODEL",
    "AOC_GENERATION_PROVIDER",
    "AOC_GENERATION_MODEL",
    "AOC_ASSESSMENT_PROVIDER",
    "AOC_ASSESSMENT_MODEL",
    "AOC_ANTHROPIC_DEFAULT_MODEL",
    "AOC_ANTHROPIC_FALLBACKS",
    "AOC_ENV_FILE_PATH",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ROLE_ENV:
        monkeypatch.delenv(key, raising=False)


def test_role_defaults():
    settings = Settings(_env_file=None)
    assert settings.transcription_provider == "openai"
    assert settings.transcription_model == "gpt-transcribe"
    assert settings.generation_provider == "openai"
    assert settings.generation_model == "gpt-6.1-sol"
    assert settings.assessment_provider == "openai"
    assert settings.assessment_model == "gpt-6.1-sol"
    assert settings.anthropic_default_model == "claude-sonnet-5-5"
    assert settings.anthropic_fallbacks is True
    assert settings.base_env_file == APP_DIR.parent / ".env"
    assert settings.env_file_path is None


def test_role_env_override(monkeypatch):
    monkeypatch.setenv("AOC_GENERATION_PROVIDER", "anthropic")
    assert Settings(_env_file=None).generation_provider == "anthropic"


def test_invalid_provider_rejected(monkeypatch):
    monkeypatch.setenv("AOC_GENERATION_PROVIDER", "foo")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
