"""Bounded retries: one module holds every limit, and each loop's call count is pinned."""

from datetime import UTC, datetime

import anthropic
import openai
import pytest
from pydantic import SecretStr
from test_assessment_support import question
from test_jobs_api import Handler, Out
from test_topics_support import make_subject, upgrade

from app.db.session import SessionLocal
from app.features.assessment.schemas import AssessmentError, AssessmentInput
from app.features.assessment.service import assess
from app.features.follow_ups import service as follow_ups
from app.features.follow_ups.prompts import follow_up_system
from app.features.jobs import service as jobs
from app.features.languages.profiles import PROFILES
from app.features.providers import limits
from app.features.providers import router as providers_router
from app.features.providers.anthropic_adapter import AnthropicTextGenerator
from app.features.providers.fakes import FakeTextGenerator
from app.features.providers.openai_adapter import OpenAITextGenerator, OpenAITranscriber
from app.features.providers.types import ProviderError
from app.features.voice import azure_adapter

KEY = SecretStr("sk-test-" + "x" * 24)
KIND = "test.retry"


class Raising:
    """A generator whose every call fails with the given error kind."""

    def __init__(self, kind: str, retryable: bool = False) -> None:
        self.kind, self.retryable = kind, retryable
        self.calls: list[str] = []

    def generate_structured(self, task, system, messages, schema, *, operation_id, **kwargs):
        self.calls.append(operation_id)
        raise ProviderError(self.kind, "openai", f"Fake {self.kind}.", retryable=self.retryable)


def test_limits():
    assert limits.SDK_MAX_RETRIES == 2
    assert limits.INVALID_OUTPUT_RETRIES == 1
    assert limits.attempt_ids("op") == ["op", "op:r1"]
    assert len(limits.attempt_ids("op")) == 1 + limits.INVALID_OUTPUT_RETRIES


def test_sdk_retry_counts(monkeypatch):
    assert OpenAITextGenerator(KEY, "gpt").client.max_retries == limits.SDK_MAX_RETRIES
    assert OpenAITranscriber(KEY, "gpt-transcribe").client.max_retries == limits.SDK_MAX_RETRIES
    assert AnthropicTextGenerator(KEY, "claude").client.max_retries == limits.SDK_MAX_RETRIES
    assert azure_adapter.MAX_RETRIES == limits.SDK_MAX_RETRIES

    built: list[int] = []

    class Client:
        def __init__(self, *, max_retries, **kwargs) -> None:
            built.append(max_retries)
            self.models = self

        def list(self, **kwargs):
            return []

    monkeypatch.setattr(openai, "OpenAI", Client)
    monkeypatch.setattr(anthropic, "Anthropic", Client)
    for provider in ("openai", "anthropic"):
        settings = type("S", (), {f"{provider}_api_key": KEY})()
        monkeypatch.setattr(providers_router, "_key_value", lambda s, p: KEY.get_secret_value())
        assert providers_router.test_key(provider, settings).ok is True
    assert built == [0, 0]  # the key test never retries


def _inp() -> AssessmentInput:
    return AssessmentInput(
        question=question(), transcript="Fotosynteza to proces…", outcome="answered"
    )


def test_assessment_invalid_output_twice_stops():
    generator = Raising("invalid_output")
    with pytest.raises(AssessmentError):
        assess(_inp(), generator, operation_id="attempt-1:assess:1", profile=PROFILES["pl"])
    assert generator.calls == limits.attempt_ids("attempt-1:assess:1")
    assert len(generator.calls) == 1 + limits.INVALID_OUTPUT_RETRIES


@pytest.mark.parametrize("kind", ["spending_limit", "auth", "bad_request", "rate_limit"])
def test_never_retried_kinds(kind):
    generator = Raising(kind, retryable=kind == "rate_limit")
    with pytest.raises(ProviderError) as raised:
        assess(_inp(), generator, operation_id="attempt-1:assess:1", profile=PROFILES["pl"])
    assert raised.value.kind == kind
    assert generator.calls == ["attempt-1:assess:1"]


def test_follow_up_invalid_output_twice_stops():
    generator = Raising("invalid_output")
    row = type("Row", (), {"id": 7, "tries": 1, "started_at": datetime.now(UTC)})()
    with pytest.raises(follow_ups._Invalid):
        follow_ups._ask_model(
            generator,
            row,
            [],
            lambda value: (value, None),
            system=follow_up_system("materials", profile=PROFILES["pl"]),
        )
    assert generator.calls == limits.attempt_ids("follow-up-7:generate:1")


@pytest.mark.parametrize("kind", ["spending_limit", "auth", "bad_request"])
def test_follow_up_never_retried_kinds(kind):
    generator = Raising(kind)
    row = type("Row", (), {"id": 7, "tries": 1, "started_at": datetime.now(UTC)})()
    with pytest.raises(ProviderError):
        follow_ups._ask_model(
            generator,
            row,
            [],
            lambda value: (value, None),
            system=follow_up_system("materials", profile=PROFILES["pl"]),
        )
    assert generator.calls == ["follow-up-7:generate:1"]


class FlakyHandler(Handler):
    """Fails step 1 once with a retryable rate limit, then works."""

    def __init__(self) -> None:
        super().__init__()
        self.generator_calls: list[str] = []
        self.failed = False

    def __call__(self, session, job, index, generator):
        self.calls.append(index)
        operation_id = f"{job.kind}:{job.id}:{index}"
        self.generator_calls.append(operation_id)
        if index == 1 and not self.failed:
            self.failed = True
            raise ProviderError("rate_limit", "openai", "Fake rate limit.", retryable=True)
        FakeTextGenerator({"t": Out(x=1)}).generate_structured(
            "t", "", [], Out, operation_id=operation_id
        )
        return {"index": index}


def test_job_step_is_not_retried(settings):
    upgrade(settings.effective_database_url)
    handler = FlakyHandler()
    jobs.register_step_handler(KIND, handler)
    try:
        with SessionLocal(settings.effective_database_url) as session:
            subject = make_subject(session)
            job = jobs.create_job(session, KIND, subject.id, {}, steps_total=3)
            job_id = job.id
        jobs.run_job(job_id, settings, FakeTextGenerator({}))
        with SessionLocal(settings.effective_database_url) as session:
            job = session.get(jobs.AiJob, job_id)
            assert (job.status, job.steps_done) == ("failed", 1)
        assert handler.calls == [0, 1]  # step 1 was called once, nothing after it
        with SessionLocal(settings.effective_database_url) as session:
            jobs.prepare_resume(session, job_id)
        jobs.run_job(job_id, settings, FakeTextGenerator({}))
        assert handler.calls == [0, 1, 1, 2]  # Resume: the failed step once more, then the rest
        with SessionLocal(settings.effective_database_url) as session:
            assert session.get(jobs.AiJob, job_id).status == "done"
    finally:
        jobs.unregister_step_handler(KIND)
