import pytest
from pydantic import ValidationError

from app.config import Settings

VOICE_ENV = (
    "AOC_VOICE_PROVIDER",
    "AOC_VOICE_NAME",
    "AOC_VOICE_RATE",
    "AOC_AZURE_VOICE_DEFAULT",
    "AOC_VOICES_DIR",
    "AOC_DATA_DIR",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in VOICE_ENV:
        monkeypatch.delenv(key, raising=False)


def test_voice_defaults():
    settings = Settings(_env_file=None)
    assert settings.voice_provider == "piper"
    assert settings.voice_name == "pl_PL-bass-high"
    assert settings.voice_rate == 1.0
    assert settings.azure_voice_default == "pl-PL-ZofiaNeural"
    assert settings.voices_dir is None


@pytest.mark.parametrize("rate", [0.4, 1.6])
def test_rate_bounds(rate):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, voice_rate=rate)
    assert Settings(_env_file=None, voice_rate=0.5).voice_rate == 0.5
    assert Settings(_env_file=None, voice_rate=1.5).voice_rate == 1.5


def test_effective_voices_dir_default_under_data(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert settings.effective_voices_dir == tmp_path / "voices"
    custom = tmp_path / "elsewhere"
    assert Settings(_env_file=None, voices_dir=custom).effective_voices_dir == custom


def test_invalid_voice_provider_rejected(monkeypatch):
    monkeypatch.setenv("AOC_VOICE_PROVIDER", "foo")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
