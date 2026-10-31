"""One real OpenAI Realtime reading. Run by hand: pytest -m live -k openai_live -q
With AOC_LIVE_VERIFY=1 the reading is also transcribed and checked word for word."""

import os

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.features.providers.openai_adapter import OpenAITranscriber
from app.features.providers.types import VoiceSpec
from app.features.voice.cache import wav_duration_ms
from app.features.voice.openai_realtime import OpenAIRealtimeReader
from app.features.voice.verbatim import compare

TEXT = "Dzień dobry. Omów przyczyny rewolucji przemysłowej."


@pytest.mark.live
def test_live_realtime_reads_polish():
    key = os.environ.get("AOC_OPENAI_API_KEY", "")
    if not key.strip():
        pytest.skip("AOC_OPENAI_API_KEY not set")
    settings = Settings(_env_file=None, base_env_file=None)
    reader = OpenAIRealtimeReader(SecretStr(key), settings.openai_voice_model)
    result = reader.synthesize(TEXT, VoiceSpec(voice="marin"), operation_id="live:voice")
    assert (wav_duration_ms(result.audio) or 0) > 1000
    assert result.usage.output_tokens > 0
    if os.environ.get("AOC_LIVE_VERIFY") == "1":
        transcriber = OpenAITranscriber(SecretStr(key), settings.transcription_model)
        heard = transcriber.transcribe(
            result.audio, "audio/wav", [], operation_id="live:verify", language="pl"
        )
        assert compare(TEXT, heard.text).ok, heard.text
