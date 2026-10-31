"""Builders for the recovery tests (no tests here); the older support files stay unedited."""

from datetime import datetime

from alembic.config import Config
from sqlalchemy import select
from test_attempts_support import ASSESS_TASK, ai_for
from test_topics_support import BACKEND

from app.db.session import SessionLocal
from app.features.providers.fakes import FakeTextGenerator, FakeTranscriber
from app.features.providers.models import UsageEventRow
from app.features.providers.registry import RecordingTextGenerator, RecordingTranscriber
from app.features.providers.types import Usage
from app.features.providers.usage import DurableUsageRecorder, InMemoryUsageRecorder, UsageEvent


def migration_config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()
    return cfg


def durable(url: str) -> DurableUsageRecorder:
    return DurableUsageRecorder(url, InMemoryUsageRecorder())


def usage_rows(url: str) -> list[UsageEventRow]:
    with SessionLocal(url) as session:
        return list(session.scalars(select(UsageEventRow).order_by(UsageEventRow.id)))


def event(operation_id: str, started_at: datetime, input_tokens: int = 10, **over) -> UsageEvent:
    fields = dict(
        operation_id=operation_id,
        task="transcription",
        role="transcription",
        provider="openai",
        model="fake",
        usage=Usage(input_tokens=input_tokens, output_tokens=2),
        status="ok",
        error_kind=None,
        started_at=started_at,
        duration_ms=5,
    )
    fields.update(over)
    return UsageEvent(**fields)


class RecordedProviders:
    """The pipeline seam with fakes wrapped in the registry's recording adapters."""

    def __init__(self, recorder) -> None:
        self.fake_transcriber = FakeTranscriber()
        self.fake_assessor = FakeTextGenerator({ASSESS_TASK: ai_for()})
        self.recorder = recorder

    def transcriber(self):
        return RecordingTranscriber(lambda: self.fake_transcriber, "openai", "fake", self.recorder)

    def assessor(self):
        return RecordingTextGenerator(
            lambda: self.fake_assessor, "openai", "fake", "assessment", self.recorder
        )

    def follow_up_generator(self):
        raise AssertionError("no follow-up is expected here")
