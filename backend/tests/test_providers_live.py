"""Real provider calls, one small call per test. Run by hand: pytest -m live -k providers -q"""

import os
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.config import Settings
from app.features.providers.anthropic_adapter import AnthropicTextGenerator
from app.features.providers.openai_adapter import (
    MIME_EXTENSIONS,
    OpenAITextGenerator,
    OpenAITranscriber,
)
from app.features.providers.registry import resolve_role
from app.features.providers.types import Message

PROMPT = "Odpowiedz jednym słowem: stolica Polski?"

_settings = Settings()

# conftest.py un-skips live tests only when a AOC_*_API_KEY environment variable exists;
# keys kept in backend/.env are exported here so that file alone is enough.
for _name, _secret in (
    ("AOC_OPENAI_API_KEY", _settings.openai_api_key),
    ("AOC_ANTHROPIC_API_KEY", _settings.anthropic_api_key),
):
    if _secret is not None and _secret.get_secret_value() and not os.environ.get(_name):
        os.environ[_name] = _secret.get_secret_value()


class Capital(BaseModel):
    answer: str


def _require(secret, name: str):
    if secret is None or not secret.get_secret_value():
        pytest.skip(f"{name} not set")
    return secret


def _ask(generator) -> str:
    result = generator.generate_structured(
        "live_check",
        "Odpowiadasz po polsku.",
        [Message(role="user", content=PROMPT)],
        Capital,
        operation_id="live-check",
        max_output_tokens=200,
    )
    return result.value.answer


@pytest.mark.live
def test_live_openai_structured_small():
    key = _require(_settings.openai_api_key, "AOC_OPENAI_API_KEY")
    provider, model = resolve_role(_settings, "generation")
    if provider != "openai":
        model = Settings.model_fields["generation_model"].default
    assert "Warszaw" in _ask(OpenAITextGenerator(key, model))


@pytest.mark.live
def test_live_anthropic_structured_small():
    key = _require(_settings.anthropic_api_key, "AOC_ANTHROPIC_API_KEY")
    provider, model = resolve_role(_settings, "generation")
    if provider != "anthropic":
        model = _settings.anthropic_default_model
    generator = AnthropicTextGenerator(key, model, fallbacks=_settings.anthropic_fallbacks)
    assert "Warszaw" in _ask(generator)


@pytest.mark.live
def test_live_openai_transcription():
    key = _require(_settings.openai_api_key, "AOC_OPENAI_API_KEY")
    audio_path = os.environ.get("AOC_LIVE_AUDIO")
    if not audio_path:
        pytest.skip("AOC_LIVE_AUDIO not set")
    path = Path(audio_path)
    mime = next(m for m, ext in MIME_EXTENSIONS.items() if ext == path.suffix.lower())
    transcriber = OpenAITranscriber(key, _settings.transcription_model)
    result = transcriber.transcribe(
        path.read_bytes(), mime, [], operation_id="live-audio", language="pl"
    )
    assert result.text.strip()
    assert result.language == "pl"
