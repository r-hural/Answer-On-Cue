import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.db.session import SessionLocal
from app.features.jobs import service
from app.features.jobs.models import AiJob
from app.features.library.models import Subject
from app.features.providers.fakes import FakeTextGenerator
from app.features.providers.types import ProviderError

BACKEND = Path(__file__).resolve().parent.parent
KIND = "test.dummy"


def upgrade(url: str) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()
    command.upgrade(cfg, "head")


class Handler:
    """Records each call; `fail` maps a step index to the exception to raise once."""

    def __init__(self, fail: dict[int, Exception] | None = None) -> None:
        self.calls: list[int] = []
        self.fail = dict(fail or {})

    def __call__(self, session, job, step_index, generator):
        self.calls.append(step_index)
        error = self.fail.pop(step_index, None)
        if error is not None:
            raise error
        return {"value": step_index * 10}


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        subject = Subject(name="Historia")
        session.add(subject)
        session.commit()
        yield session, subject.id


@pytest.fixture
def handler():
    h = Handler()
    service.register_step_handler(KIND, h)
    yield h
    service.unregister_step_handler(KIND)


def _job(settings, job_id: int) -> AiJob:
    with SessionLocal(settings.effective_database_url) as session:
        return session.get(AiJob, job_id)


def test_steps_run_in_order_and_done(settings, db, handler):
    session, subject_id = db
    job = service.create_job(session, KIND, subject_id, {"a": 1}, steps_total=3)
    assert job.status == "queued"
    service.run_job(job.id, settings, FakeTextGenerator({}))
    assert handler.calls == [0, 1, 2]
    done = _job(settings, job.id)
    assert done.status == "done"
    assert done.steps_done == 3
    assert json.loads(done.state_json)["steps"] == {
        "0": {"value": 0},
        "1": {"value": 10},
        "2": {"value": 20},
    }
    assert done.error_message is None


def test_failure_marks_failed_keeps_completed_steps(settings, db, handler):
    session, subject_id = db
    handler.fail[1] = RuntimeError("boom")
    job = service.create_job(session, KIND, subject_id, {}, steps_total=3)
    service.run_job(job.id, settings, FakeTextGenerator({}))
    failed = _job(settings, job.id)
    assert failed.status == "failed"
    assert failed.steps_done == 1
    assert list(json.loads(failed.state_json)["steps"]) == ["0"]
    assert handler.calls == [0, 1]
    assert failed.error_message  # a generic message, never the raw exception
    assert "boom" not in failed.error_message


def test_resume_skips_completed_steps(settings, db, handler):
    session, subject_id = db
    handler.fail[1] = ProviderError("timeout", "openai", "The provider did not respond in time.")
    job = service.create_job(session, KIND, subject_id, {}, steps_total=3)
    service.run_job(job.id, settings, FakeTextGenerator({}))
    assert _job(settings, job.id).status == "failed"
    service.run_job(job.id, settings, FakeTextGenerator({}))
    assert handler.calls == [0, 1, 1, 2]
    resumed = _job(settings, job.id)
    assert resumed.status == "done"
    assert resumed.steps_done == 3
    assert resumed.error_message is None


def test_provider_error_message_in_job(settings, db, handler):
    session, subject_id = db
    error = ProviderError(
        "auth", "openai", "The key was rejected by the provider. sk-abcdefgh12345"
    )
    handler.fail[0] = error
    job = service.create_job(session, KIND, subject_id, {}, steps_total=2)
    service.run_job(job.id, settings, FakeTextGenerator({}))
    failed = _job(settings, job.id)
    assert failed.error_message == str(error)
    assert "sk-abcdefgh12345" not in failed.error_message
    assert failed.error_message.startswith("The key was rejected")


def test_partial_when_some_steps_failed_but_continuable(settings, db, handler):
    session, subject_id = db
    handler.fail[1] = service.StepSkipped("Passage skipped.")
    job = service.create_job(session, KIND, subject_id, {}, steps_total=3)
    service.run_job(job.id, settings, FakeTextGenerator({}))
    partial = _job(settings, job.id)
    assert partial.status == "partial"
    assert partial.steps_done == 2
    assert partial.error_message == "Passage skipped."
    assert handler.calls == [0, 1, 2]
    service.run_job(job.id, settings, FakeTextGenerator({}))
    assert handler.calls == [0, 1, 2, 1]
    assert _job(settings, job.id).status == "done"


def test_job_api_get_and_resume(settings, db, handler, client):
    from app.features.providers.registry import _generation_dep

    client.app.dependency_overrides[_generation_dep] = lambda: FakeTextGenerator({})
    session, subject_id = db
    handler.fail[0] = RuntimeError("boom")
    job = service.create_job(session, KIND, subject_id, {}, steps_total=2)
    service.run_job(job.id, settings, FakeTextGenerator({}))
    body = client.get(f"/api/jobs/{job.id}").json()
    assert body["status"] == "failed"
    assert set(body) == {
        "id", "kind", "status", "steps_total", "steps_done", "error_message", "summary",
        "subject_id", "document_version_id", "resumable", "usage", "created_at", "updated_at",
        "subject_name", "document_title", "chapter_id",
    }  # fmt: skip
    assert body["resumable"] is True
    assert client.post(f"/api/jobs/{job.id}/resume").status_code == 202
    assert client.get(f"/api/jobs/{job.id}").json()["status"] == "done"
    response = client.post(f"/api/jobs/{job.id}/resume")
    assert response.status_code == 409
    assert client.get("/api/jobs/99999").status_code == 404


def _age(session, job_id: int, minutes: int) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    old = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=minutes)
    session.execute(update(AiJob).where(AiJob.id == job_id).values(updated_at=old))
    session.commit()


def test_stale_queued_job_resumable_and_not_blocking(settings, db, handler, client):
    from app.features.providers.registry import _generation_dep

    client.app.dependency_overrides[_generation_dep] = lambda: FakeTextGenerator({})
    session, subject_id = db
    job = service.create_job(session, KIND, subject_id, {}, steps_total=1, document_version_id=None)
    assert client.post(f"/api/jobs/{job.id}/resume").status_code == 409  # fresh: may still start
    _age(session, job.id, 30)
    session.expire_all()
    assert not service.is_live(session.get(AiJob, job.id))
    assert client.post(f"/api/jobs/{job.id}/resume").status_code == 202
    assert client.get(f"/api/jobs/{job.id}").json()["status"] == "done"


def test_running_job_of_another_process_not_resumable_until_stale(settings, db, handler, client):
    from sqlalchemy import update

    from app.features.providers.registry import _generation_dep

    client.app.dependency_overrides[_generation_dep] = lambda: FakeTextGenerator({})
    session, subject_id = db
    job = service.create_job(session, KIND, subject_id, {}, steps_total=1)
    session.execute(update(AiJob).where(AiJob.id == job.id).values(status="running"))
    session.commit()
    assert client.post(f"/api/jobs/{job.id}/resume").status_code == 409
    _age(session, job.id, 30)
    assert client.post(f"/api/jobs/{job.id}/resume").status_code == 202
