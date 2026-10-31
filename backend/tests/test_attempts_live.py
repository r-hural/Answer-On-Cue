"""One real attempt through the pipeline. Run by hand: pytest -m live -k attempts -q

speech_mid.wav is English synthetic speech: this checks only the round trip (upload → real
transcription → real assessment → stored result), not the quality of either.
"""

import os

import pytest
from test_attempts_support import answered_attempt, override_providers, post_answer, result
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.config import Settings
from app.db.session import SessionLocal
from app.features.attempts.providers import RegistryProviders
from app.features.providers.usage import InMemoryUsageRecorder

MAX_CALLS = 2

_settings = Settings()

# conftest.py un-skips live tests only when a AOC_*_API_KEY environment variable exists;
# keys kept in backend/.env are exported here so that file alone is enough.
for _name, _secret in (
    ("AOC_OPENAI_API_KEY", _settings.openai_api_key),
    ("AOC_ANTHROPIC_API_KEY", _settings.anthropic_api_key),
):
    if _secret is not None and _secret.get_secret_value() and not os.environ.get(_name):
        os.environ[_name] = _secret.get_secret_value()


class Capped:
    """The real providers, refusing any call beyond MAX_CALLS in total."""

    def __init__(self, inner: RegistryProviders) -> None:
        self.inner = inner
        self.calls = 0

    def _count(self) -> None:
        self.calls += 1
        if self.calls > MAX_CALLS:
            pytest.fail(f"the live test would make more than {MAX_CALLS} calls")

    def transcriber(self):
        real = self.inner.transcriber()
        capped = self

        class Transcriber:
            def transcribe(self, *args, **kwargs):
                capped._count()
                return real.transcribe(*args, **kwargs)

        return Transcriber()

    def assessor(self):
        real = self.inner.assessor()
        capped = self

        class Assessor:
            def generate_structured(self, *args, **kwargs):
                capped._count()
                return real.generate_structured(*args, **kwargs)

        return Assessor()


@pytest.mark.live
def test_live_attempt_smoke(client, settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        world = make_world(session)
    live = Settings(
        _env_file=None,
        base_env_file=None,
        data_dir=settings.data_dir,
        frontend_dist=settings.frontend_dist,
        env_file_path=settings.env_file_path,
        openai_api_key=_settings.openai_api_key,
        anthropic_api_key=_settings.anthropic_api_key,
    )
    providers = Capped(RegistryProviders(live, InMemoryUsageRecorder()))
    override_providers(client.app, providers)
    _, attempt = answered_attempt(client, world)
    post_answer(client, attempt)
    body = result(client, attempt)
    assert body["status"] == "ready", body["failure"]
    assert body["assessment"]["status"] in ("assessed", "no_speech")
    assert providers.calls <= MAX_CALLS
