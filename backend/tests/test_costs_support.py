"""Builders for the cost-control tests (no tests here); the older support files stay unedited."""

import threading
from datetime import UTC, datetime, timedelta, timezone

from test_recovery_support import durable, migration_config, usage_rows  # noqa: F401

from app.db.session import SessionLocal
from app.features.providers.fakes import FakeSynthesizer, FakeTextGenerator, FakeTranscriber
from app.features.providers.models import UsageEventRow
from app.features.providers.types import Provenance, Structured, Transcript, Usage

ZONE = timezone(timedelta(hours=13))  # a fixed zone: Windows has no tz database
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=ZONE)


class PaidTextGenerator(FakeTextGenerator):
    """A fake that reports usage, like a real provider would."""

    def __init__(self, responses, *, input_tokens=10_000, output_tokens=1_000, **kwargs) -> None:
        super().__init__(responses, **kwargs)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.gate: threading.Event | None = None  # set: each call waits for it

    def generate_structured(self, task, system, messages, schema, **kwargs) -> Structured:
        if self.gate is not None:
            self.gate.wait(5)
        result = super().generate_structured(task, system, messages, schema, **kwargs)
        return result.model_copy(
            update={
                "usage": Usage(input_tokens=self.input_tokens, output_tokens=self.output_tokens)
            }
        )


class PaidTranscriber(FakeTranscriber):
    def __init__(self, seconds: float = 60.0, **kwargs) -> None:
        super().__init__(**kwargs)
        self.seconds = seconds

    def transcribe(self, audio, mime, glossary, *, operation_id, language="pl"):
        super().transcribe(audio, mime, glossary, operation_id=operation_id, language=language)
        return Transcript(
            text=self.text,
            language=language,
            usage=Usage(audio_seconds=self.seconds),
            provenance=Provenance(
                provider="openai", model="gpt-transcribe", operation_id=operation_id
            ),
        )


class FakeEngine(FakeSynthesizer):
    """FakeSynthesizer plus the engine version the voice service asks for."""

    def engine_version(self, voice: str) -> str:
        return "fake-1"


def seed_usage(
    url: str,
    cost_usd: float | None,
    *,
    started_at: datetime = NOW,
    operation: str = "attempt.assess",
    status: str = "ok",
    estimated_usd: float | None = None,
    **over,
) -> UsageEventRow:
    """One usage row with a known cost, written straight to the table."""
    fields = dict(
        operation_id=f"seed:{operation}",
        operation=operation,
        task=operation,
        role="assessment",
        provider="openai",
        model="gpt-6.1-sol",
        status=status,
        error_kind=None,
        input_tokens=0,
        output_tokens=0,
        audio_seconds=0.0,
        characters=0,
        started_at=started_at.astimezone(UTC),
        duration_ms=5,
        estimated_usd=estimated_usd,
        cost_usd=cost_usd,
    )
    fields.update(over)
    with SessionLocal(url) as session:
        row = UsageEventRow(**fields)
        session.add(row)
        session.commit()
        return row
