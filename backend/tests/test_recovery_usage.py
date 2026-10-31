"""Usage events persist in usage_event, with the operation, round and subject."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytest
from alembic import command
from conftest import local_client
from fastapi import Depends
from sqlalchemy import create_engine, func, inspect, select, text
from test_attempts_support import (
    answered_attempt,
    override_providers,
    post_answer,
    result,
    wav,
)
from test_recovery_support import (
    RecordedProviders,
    durable,
    event,
    migration_config,
    usage_rows,
)
from test_rounds_support import make_world
from test_topics_support import (
    fake_consolidation,
    fake_section_topics,
    make_subject,
    make_version,
    upgrade,
)

from app.db.session import SessionLocal
from app.features.attempts.providers import PipelineProviders, get_pipeline_providers
from app.features.jobs.models import AiJob
from app.features.providers.fakes import FakeTextGenerator, FakeTranscriber
from app.features.providers.models import UsageEventRow
from app.features.providers.registry import (
    RecordingTextGenerator,
    RecordingTranscriber,
    _generation_dep,
)
from app.features.providers.types import Provenance, ProviderError, Transcript, Usage
from app.features.providers.usage import (
    DurableUsageRecorder,
    InMemoryUsageRecorder,
    OperationRef,
    parse_operation,
)
from app.features.rounds.models import Round

RECORDER_LOGGER = "app.features.providers.usage"
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    return make_world(db)


@pytest.fixture
def url(settings):
    return settings.effective_database_url


class PaidTranscriber(FakeTranscriber):
    """A fake that reports usage and a request id, like a real provider would."""

    def transcribe(self, audio, mime, glossary, *, operation_id, language="pl"):
        super().transcribe(audio, mime, glossary, operation_id=operation_id, language=language)
        return Transcript(
            text=self.text,
            language=language,
            usage=Usage(audio_seconds=12.5),
            provenance=Provenance(
                provider="openai", model="whisper-x", operation_id=operation_id, request_id="req-1"
            ),
        )


def test_usage_event_migration_and_downgrade(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/m.db"
    cfg = migration_config(url)
    command.upgrade(cfg, "head")
    inspector = inspect(create_engine(url))
    columns = {c["name"] for c in inspector.get_columns("usage_event")}
    assert columns == {
        "id",
        "operation_id",
        "operation",
        "task",
        "role",
        "provider",
        "model",
        "status",
        "error_kind",
        "input_tokens",
        "output_tokens",
        "audio_seconds",
        "characters",
        "request_id",
        "started_at",
        "duration_ms",
        "subject_id",
        "round_id",
        "attempt_id",
        "follow_up_id",
        "job_id",
        "estimated_usd",
        "cost_usd",
    }
    indexes = {i["name"] for i in inspector.get_indexes("usage_event")}
    assert indexes == {
        "ix_usage_event_operation_id",
        "ix_usage_event_started_at",
        "ix_usage_event_subject_id",
        "ix_usage_event_round_id",
    }
    assert inspector.get_foreign_keys("usage_event") == []
    with create_engine(url).begin() as conn:
        conn.execute(text("INSERT INTO subject (name) VALUES ('Historia')"))
    command.downgrade(cfg, "0010_adaptive")
    inspector = inspect(create_engine(url))
    assert "usage_event" not in inspector.get_table_names()
    with create_engine(url).connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM subject")).scalar_one() == 1
    command.upgrade(cfg, "head")
    assert "usage_event" in inspect(create_engine(url)).get_table_names()


@pytest.mark.parametrize(
    ("operation_id", "task", "expected"),
    [
        ("attempt-7:transcribe:2", "transcription", OperationRef("attempt.transcribe", 7)),
        ("attempt-7:assess:1", "assessment.assess", OperationRef("attempt.assess", 7)),
        ("attempt-7:assess:1:r1", "assessment.assess", OperationRef("attempt.assess", 7)),
        (
            "follow-up-3:generate:1",
            "follow_ups.generate",
            OperationRef("follow_up.generate", follow_up_id=3),
        ),
        (
            "follow-up-3:generate:1:r1",
            "follow_ups.generate",
            OperationRef("follow_up.generate", follow_up_id=3),
        ),
        (
            "topics.extract:4:0",
            "topics.extract_section",
            OperationRef("topics.extract", job_id=4),
        ),
        (
            "questions.generate:5:2:verify",
            "questions.verify",
            OperationRef("questions.generate", job_id=5),
        ),
        ("eval-x:case1", "assessment.assess", OperationRef("assessment.assess")),
        ("0f" * 16, "voice.synthesize", OperationRef("voice.synthesize")),
    ],
)
def test_parse_operation(operation_id, task, expected):
    assert parse_operation(operation_id, task) == expected


def test_durable_recorder_writes_row_with_context(client, world, url):
    rid, attempt_id = answered_attempt(client, world)
    recorder = durable(url)
    transcriber = RecordingTranscriber(PaidTranscriber, "openai", "whisper-x", recorder)
    transcriber.transcribe(
        wav("speech_mid.wav"),
        "audio/wav",
        [],
        operation_id=f"attempt-{attempt_id}:transcribe:1",
        language="pl",
    )
    [row] = usage_rows(url)
    assert (row.operation, row.attempt_id, row.round_id, row.subject_id) == (
        "attempt.transcribe",
        attempt_id,
        rid,
        world["subject"].id,
    )
    assert (row.status, row.error_kind, row.role, row.provider, row.model) == (
        "ok",
        None,
        "transcription",
        "openai",
        "whisper-x",
    )
    assert (row.audio_seconds, row.input_tokens, row.request_id) == (12.5, 0, "req-1")
    assert row.operation_id == f"attempt-{attempt_id}:transcribe:1"
    [memory] = recorder.memory.events
    assert memory.operation_id == row.operation_id and memory.request_id == "req-1"


def test_error_call_is_recorded(client, world, url):
    _, attempt_id = answered_attempt(client, world)
    error = ProviderError("server", "openai", "Provider failure: server.")
    transcriber = RecordingTranscriber(
        lambda: FakeTranscriber(error=error), "openai", "whisper-x", durable(url)
    )
    with pytest.raises(ProviderError) as raised:
        transcriber.transcribe(
            b"x", "audio/wav", [], operation_id=f"attempt-{attempt_id}:transcribe:1", language="pl"
        )
    assert raised.value is error
    [row] = usage_rows(url)
    assert (row.status, row.error_kind, row.request_id) == ("error", "server", None)


def test_recorder_never_raises(tmp_path, caplog):
    empty = f"sqlite:///{tmp_path.as_posix()}/empty.db"
    create_engine(empty).connect().close()
    memory = InMemoryUsageRecorder()
    recorder = DurableUsageRecorder(empty, memory)
    with caplog.at_level(logging.WARNING, logger=RECORDER_LOGGER):
        recorder.record(event("attempt-1:transcribe:1", T0))
    assert len(memory.events) == 1
    assert any("was not stored" in r.getMessage() for r in caplog.records)


def test_registry_dependencies_use_the_durable_recorder(client, settings):
    app = client.app
    assert get_pipeline_providers not in app.dependency_overrides  # only get_settings is

    @app.get("/api/_test/providers")
    def providers_route(
        providers: Annotated[PipelineProviders, Depends(get_pipeline_providers)],
    ) -> dict[str, str]:
        return {
            "providers": type(providers).__name__,
            "recorder": type(providers.recorder).__name__,
            "url": providers.recorder.database_url,
        }

    # the SPA/api catch-all routes are registered first; move the test route to the front
    app.router.routes.insert(0, app.router.routes.pop())
    assert client.get("/api/_test/providers").json() == {
        "providers": "RegistryProviders",
        "recorder": "DurableUsageRecorder",
        "url": settings.effective_database_url,
    }


def test_usage_survives_restart_and_subject_deletion(settings, client, world, db, url):
    rid, attempt_id = answered_attempt(client, world)
    recorder = durable(url)
    recorder.record(event(f"attempt-{attempt_id}:transcribe:1", T0))
    recorder.record(event(f"attempt-{attempt_id}:assess:1", T0, task="assessment.assess"))
    durable(url)  # a "restart": a new in-memory recorder
    assert len(usage_rows(url)) == 2
    db.get(Round, rid).status = "finished"
    db.commit()
    subject_id = world["subject"].id
    response = local_client(client.app).delete(f"/api/subjects/{subject_id}?confirm=true")
    assert response.status_code == 200, response.text
    rows = usage_rows(url)
    assert [r.subject_id for r in rows] == [None, None]  # links cleared
    assert [r.round_id for r in rows] == [None, None]


def test_usage_groups_per_operation_round_subject_month(client, db, url):
    first = make_world(db)
    first["subject"].name = "Biologia"  # make_world always names its subject "Historia"
    db.commit()
    second = make_world(db)
    r1, a1 = answered_attempt(client, first)
    r2, a2 = answered_attempt(client, second)
    recorder = durable(url)
    october, november = T0, T0 + timedelta(days=35)
    recorder.record(event(f"attempt-{a1}:transcribe:1", october, 10))
    recorder.record(event(f"attempt-{a1}:transcribe:2", october, 20))
    recorder.record(event(f"attempt-{a1}:assess:1", november, 40, task="assessment.assess"))
    recorder.record(event(f"attempt-{a2}:transcribe:1", october, 80))
    recorder.record(event(f"attempt-{a2}:transcribe:1", november, 160))
    month = func.strftime("%Y-%m", UsageEventRow.started_at)
    with SessionLocal(url) as session:
        groups = session.execute(
            select(
                UsageEventRow.operation,
                UsageEventRow.round_id,
                UsageEventRow.subject_id,
                month,
                func.sum(UsageEventRow.input_tokens),
            )
            .group_by(
                UsageEventRow.operation, UsageEventRow.round_id, UsageEventRow.subject_id, month
            )
            .order_by(UsageEventRow.subject_id, month, UsageEventRow.operation)
        ).all()
    s1, s2 = first["subject"].id, second["subject"].id
    assert [tuple(g) for g in groups] == [
        ("attempt.transcribe", r1, s1, "2026-10", 30),
        ("attempt.assess", r1, s1, "2026-11", 40),
        ("attempt.transcribe", r2, s2, "2026-10", 80),
        ("attempt.transcribe", r2, s2, "2026-11", 160),
    ]


# ---- integration: real runs write their rows ----


def no_recorder_warning(caplog) -> bool:
    return not [
        r for r in caplog.records if r.name == RECORDER_LOGGER and r.levelno >= logging.WARNING
    ]


def test_pipeline_run_writes_usage_rows(client, world, url, caplog):
    providers = RecordedProviders(durable(url))
    override_providers(client.app, providers)
    rid, attempt_id = answered_attempt(client, world)
    with caplog.at_level(logging.WARNING, logger=RECORDER_LOGGER):
        events, audio = post_answer(client, attempt_id)
        assert events.status_code == 200 and audio.status_code == 201
        assert result(client, attempt_id)["status"] == "ready"
    rows = usage_rows(url)
    assert [r.operation for r in rows] == ["attempt.transcribe", "attempt.assess"]
    for row in rows:
        assert (row.attempt_id, row.round_id, row.subject_id) == (
            attempt_id,
            rid,
            world["subject"].id,
        )
    assert no_recorder_warning(caplog)


def test_job_run_writes_usage_row(client, db, url, caplog):
    subject = make_subject(db)
    version, _ = make_version(db, subject.id, count=10, excluded=())
    fake = FakeTextGenerator(
        {"topics.extract_section": fake_section_topics, "topics.consolidate": fake_consolidation}
    )
    generator = RecordingTextGenerator(lambda: fake, "openai", "fake", "generation", durable(url))
    client.app.dependency_overrides[_generation_dep] = lambda: generator
    with caplog.at_level(logging.WARNING, logger=RECORDER_LOGGER):
        response = client.post("/api/topics/extract", json={"document_version_id": version.id})
        assert response.status_code == 202, response.text
    job = db.scalar(select(AiJob).order_by(AiJob.id.desc()))
    db.refresh(job)
    assert job.status == "done"
    [row] = usage_rows(url)
    assert (row.operation, row.job_id, row.subject_id, row.round_id) == (
        "topics.extract",
        job.id,
        subject.id,
        None,
    )
    assert no_recorder_warning(caplog)
