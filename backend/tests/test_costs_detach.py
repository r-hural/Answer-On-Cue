"""Usage rows of a deleted subject, and rows kept across a restore, lose their links."""

from datetime import UTC, datetime

import pytest
from conftest import local_client
from sqlalchemy import func, select
from test_backup_archive import apply, backup_path, make_backup, upload
from test_backup_support import subject_with_history
from test_costs_support import durable, seed_usage, usage_rows
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.costs.spending import month_of, spent_between
from app.features.jobs import service as jobs
from app.features.library.models import Subject
from app.features.providers.models import UsageEventRow
from app.features.providers.types import Usage
from app.features.providers.usage import UsageEvent
from app.main import create_app

LINKS = ("subject_id", "round_id", "attempt_id", "follow_up_id", "job_id")
AT = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def api(settings, db):
    return local_client(create_app(settings))


def paid(operation_id: str, task: str = "assessment.assess") -> UsageEvent:
    return UsageEvent(
        operation_id=operation_id,
        task=task,
        role="assessment",
        provider="openai",
        model="gpt-6.1-sol",
        usage=Usage(input_tokens=1000, output_tokens=100),
        status="ok",
        error_kind=None,
        started_at=AT,
        duration_ms=5,
        estimated_usd=0.01,
    )


def links(row: UsageEventRow) -> tuple:
    return tuple(getattr(row, name) for name in LINKS)


def two_subjects(db, settings) -> tuple[dict, dict]:
    first = subject_with_history(db, settings, "Historia")
    second = subject_with_history(db, settings, "Biologia")
    recorder = durable(settings.effective_database_url)
    for world in (first, second):
        attempt = world["attempts"][0]
        recorder.record(paid(f"attempt-{attempt.id}:transcribe:1", task="transcription"))
        recorder.record(paid(f"attempt-{attempt.id}:assess:1"))
        job = jobs.create_job(db, "topics.extract", world["subject"].id, {}, steps_total=1)
        recorder.record(paid(f"topics.extract:{job.id}:0", task="topics.extract_section"))
    return first, second


def test_subject_deletion_detaches_usage(api, db, settings):
    url = settings.effective_database_url
    first, second = two_subjects(db, settings)
    before = {row.id: row for row in usage_rows(url)}
    kept = {row.id: links(row) for row in before.values() if row.subject_id == second["subject"].id}
    assert all(link[0] is not None and (link[2] or link[4]) for link in kept.values())
    response = api.delete(f"/api/subjects/{first['subject'].id}?confirm=true")
    assert response.status_code == 200, response.text
    after = usage_rows(url)
    assert len(after) == len(before) == 6
    for row in after:
        old = before[row.id]
        assert (row.input_tokens, row.output_tokens, row.cost_usd, row.estimated_usd) == (
            old.input_tokens,
            old.output_tokens,
            old.cost_usd,
            old.estimated_usd,
        )
        assert (row.operation, row.started_at) == (old.operation, old.started_at)
        if row.id in kept:
            assert links(row) == kept[row.id]
        else:
            assert links(row) == (None,) * len(LINKS)
    assert len(kept) == 3


def test_reused_id_gets_no_old_usage(api, db, settings):
    url = settings.effective_database_url
    _, second = two_subjects(db, settings)
    old_id = second["subject"].id  # the highest id
    assert api.delete(f"/api/subjects/{old_id}?confirm=true").status_code == 200
    response = api.post("/api/subjects", json={"name": "Chemia", "language": "pl"})
    assert response.status_code == 201, response.text
    assert response.json()["id"] == old_id  # SQLite reuses the id
    assert [row for row in usage_rows(url) if row.subject_id == old_id] == []
    assert sum(1 for row in usage_rows(url) if row.subject_id is None) == 3


def test_deletion_preview_unchanged(api, db, settings):
    world = subject_with_history(db, settings)
    sid = world["subject"].id
    plain = api.get(f"/api/subjects/{sid}/deletion-preview").json()
    durable(settings.effective_database_url).record(
        paid(f"attempt-{world['attempts'][0].id}:assess:1")
    )
    assert api.get(f"/api/subjects/{sid}/deletion-preview").json() == plain


def test_restore_detaches_kept_rows(api, db, settings):
    history = subject_with_history(db, settings)
    url = settings.effective_database_url
    attempt = history["attempts"][0]
    backup = make_backup(api)
    durable(url).record(paid(f"attempt-{attempt.id}:assess:1"))
    seed_usage(url, 0.25, started_at=AT, subject_id=history["subject"].id, round_id=7)
    linked = usage_rows(url)[0]
    assert linked.subject_id == history["subject"].id and linked.attempt_id == attempt.id
    _, start, end = month_of("2026-10", UTC)
    total = spent_between(db, start, end)
    token = upload(api, backup_path(settings, backup)).json()["token"]
    assert apply(api, token).status_code == 200
    db.expire_all()
    rows = usage_rows(url)
    assert len(rows) == 2
    assert all(links(row) == (None,) * len(LINKS) for row in rows)
    assert spent_between(db, start, end) == total
    assert db.scalar(select(func.count()).select_from(Subject)) == 1
