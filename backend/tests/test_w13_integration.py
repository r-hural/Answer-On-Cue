"""W13 integration: T30 language-core meets T31 openai-voice."""

import pytest
from test_languages_api import by_code, db  # noqa: F401  (fixture)

from app.features.voice.catalog import OPENAI_VOICES


@pytest.mark.parametrize("code", ["pl", "de"])
def test_languages_accept_the_openai_voice(client, db, code):  # noqa: F811
    voice = next(iter(OPENAI_VOICES))
    response = client.put(
        f"/api/languages/{code}", json={"voice_provider": "openai", "voice_name": voice}
    )
    assert response.status_code == 200, response.text
    stored = by_code(client, code)
    assert (stored["voice_provider"], stored["voice_name"]) == ("openai", voice)
