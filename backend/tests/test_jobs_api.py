from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel
from test_questions_support import QuestionFaker, all_good
from test_topics_support import make_subject, make_topic, make_version, upgrade

from app.db.session import SessionLocal
from app.features.jobs import service
from app.features.jobs.models import AiJob
from app.features.providers.fakes import FakeTextGenerator
from app.features.providers.registry import RecordingTextGenerator, _generation_dep
from app.features.providers.types import Usage
from app.features.providers.usage import InMemoryUsageRecorder

KIND = "test.usage"


class Out(BaseModel):
    x: int


class PaidGenerator:
    """A fake that reports tokens, like a real provider would."""

    def __init__(self) -> None:
        self.inner = FakeTextGenerator({"t": Out(x=1)})

    def generate_structured(self, *args, **kwargs):
        result = self.inner.generate_structured(*args, **kwargs)
        result.usage = Usage(input_tokens=100, output_tokens=20)
        return result


class Handler:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def __call__(self, session, job, index, generator):
        self.calls.append(index)
        generator.generate_structured("t", "", [], Out, operation_id=f"{job.kind}:{job.id}:{index}")
        return {"index": index}


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def recorder(monkeypatch):
    recorder = InMemoryUsageRecorder()
    monkeypatch.setattr(service, "get_usage_recorder", lambda: recorder)
    return recorder


@pytest.fixture
def handler():
    handler = Handler()
    service.register_step_handler(KIND, handler)
    yield handler
    service.unregister_step_handler(KIND)


def paid(recorder) -> RecordingTextGenerator:
    return RecordingTextGenerator(PaidGenerator, "openai", "gpt-x", "generation", recorder)


def test_list_jobs_newest_first_by_subject(db, client):
    first, second = make_subject(db, "Historia"), make_subject(db, "Biologia")
    ids = [service.create_job(db, KIND, s.id, {}, 1).id for s in (first, second, first, first)]
    body = client.get(f"/api/jobs?subject_id={first.id}").json()
    assert [job["id"] for job in body] == [ids[3], ids[2], ids[0]]
    assert body[0]["subject_id"] == first.id and body[0]["document_version_id"] is None
    assert len(client.get("/api/jobs").json()) == 4
    assert [job["id"] for job in client.get(f"/api/jobs?subject_id={first.id}&limit=1").json()] == [
        ids[3]
    ]


def test_usage_after_fake_job(settings, db, client, handler, recorder):
    subject = make_subject(db)
    job = service.create_job(db, KIND, subject.id, {}, steps_total=3)
    other = service.create_job(db, KIND, subject.id, {}, steps_total=1)
    service.run_job(job.id, settings, paid(recorder))
    service.run_job(other.id, settings, paid(recorder))
    body = client.get(f"/api/jobs/{job.id}").json()
    assert body["status"] == "done"
    assert body["usage"] == [
        {
            "provider": "openai",
            "model": "fake",
            "calls": 3,
            "errors": 0,
            "input_tokens": 300,
            "output_tokens": 60,
        }
    ]


def test_usage_empty_with_new_recorder(settings, db, client, handler, recorder, monkeypatch):
    subject = make_subject(db)
    job = service.create_job(db, KIND, subject.id, {}, steps_total=1)
    service.run_job(job.id, settings, paid(recorder))
    assert client.get(f"/api/jobs/{job.id}").json()["usage"] != []
    monkeypatch.setattr(service, "get_usage_recorder", lambda: InMemoryUsageRecorder())
    assert client.get(f"/api/jobs/{job.id}").json()["usage"] == []


def test_orphaned_running_job_is_resumable(settings, db, client, handler, recorder):
    subject = make_subject(db)
    live = service.create_job(db, KIND, subject.id, {}, steps_total=2)
    live.status = "running"
    db.commit()
    assert client.get(f"/api/jobs/{live.id}").json()["resumable"] is False  # touched just now
    orphan = service.create_job(db, KIND, subject.id, {}, steps_total=2)
    orphan.status = "running"
    orphan.updated_at = datetime.now(UTC) - timedelta(minutes=30)
    db.commit()
    body = client.get(f"/api/jobs/{orphan.id}").json()
    assert body["status"] == "running" and body["resumable"] is True
    client.app.dependency_overrides[_generation_dep] = lambda: paid(recorder)
    assert client.post(f"/api/jobs/{orphan.id}/resume").status_code == 202
    assert client.get(f"/api/jobs/{orphan.id}").json()["status"] == "done"
    assert client.get(f"/api/jobs/{orphan.id}").json()["resumable"] is False


def test_resume_skips_done_steps(settings, db, client, handler, recorder):
    subject = make_subject(db)
    job = service.create_job(db, KIND, subject.id, {}, steps_total=3)
    fail = {"left": 1}
    calls = {"n": 0}
    original = handler.__call__

    def flaky(session, job_, index, generator):
        if index == 1 and fail["left"]:
            fail["left"] -= 1
            raise RuntimeError("cut off")
        calls["n"] += 1
        return original(session, job_, index, generator)

    service.register_step_handler(KIND, flaky)
    service.run_job(job.id, settings, paid(recorder))
    assert client.get(f"/api/jobs/{job.id}").json()["resumable"] is True
    client.app.dependency_overrides[_generation_dep] = lambda: paid(recorder)
    client.post(f"/api/jobs/{job.id}/resume")
    assert handler.calls == [0, 1, 2]  # step 0 was not paid for again
    body = client.get(f"/api/jobs/{job.id}").json()
    assert body["status"] == "done" and body["usage"][0]["calls"] == 3


def test_second_generation_start_returns_live_job(db, client):
    subject = make_subject(db)
    _, passages = make_version(db, subject.id, count=4, excluded=())
    make_topic(db, subject.id, "Ateny", passages, ("Wyjaśnić demokrację",), (), 0)
    fake = FakeTextGenerator({"questions.generate": QuestionFaker(), "questions.verify": all_good})
    client.app.dependency_overrides[_generation_dep] = lambda: fake
    body = {"subject_id": subject.id, "per_topic": 1}
    live = service.create_job(db, "questions.generate", subject.id, {}, steps_total=1)
    live.status = "running"
    db.commit()
    response = client.post("/api/questions/generate", json=body)
    assert response.status_code == 202
    assert response.json() == {"job_id": live.id}
    assert fake.calls == []  # the live job is not run a second time
    assert db.query(AiJob).count() == 1
    live.status = "done"
    db.commit()
    again = client.post("/api/questions/generate", json=body)
    assert again.json()["job_id"] != live.id
    assert db.query(AiJob).count() == 2


def test_generation_start_with_other_policy_than_live_job_conflicts(db, client):
    subject = make_subject(db)
    _, passages = make_version(db, subject.id, count=4, excluded=())
    make_topic(db, subject.id, "Ateny", passages, ("Wyjaśnić demokrację",), (), 0)
    fake = FakeTextGenerator({"questions.generate": QuestionFaker(), "questions.verify": all_good})
    client.app.dependency_overrides[_generation_dep] = lambda: fake
    live = service.create_job(
        db, "questions.generate", subject.id, {"source_policy": "materials"}, steps_total=1
    )
    live.status = "running"
    db.commit()
    body = {"subject_id": subject.id, "per_topic": 1, "source_policy": "expanded"}
    response = client.post("/api/questions/generate", json=body)
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "A question batch with a different source policy is already running. Wait for it to finish."
    )
    assert fake.calls == [] and db.query(AiJob).count() == 1
    same = client.post("/api/questions/generate", json={**body, "source_policy": "materials"})
    assert same.status_code == 202 and same.json() == {"job_id": live.id}


def test_live_jobs_across_subjects_with_names(db, client):
    first, second = make_subject(db, "Historia"), make_subject(db, "Biologia")
    version, _ = make_version(db, first.id, count=2, excluded=())
    queued = service.create_job(db, KIND, first.id, {}, 9, document_version_id=version.id)
    running = service.create_job(db, KIND, second.id, {}, 3)
    running.status, running.steps_done = "running", 1
    done = service.create_job(db, KIND, second.id, {}, 1)
    done.status = "done"
    orphan = service.create_job(db, KIND, first.id, {}, 2)
    orphan.status = "running"
    orphan.updated_at = datetime.now(UTC) - timedelta(minutes=30)
    db.commit()
    body = client.get("/api/jobs?live=true").json()
    assert [job["id"] for job in body] == [running.id, queued.id]
    assert [(job["subject_name"], job["document_title"]) for job in body] == [
        ("Biologia", None),
        ("Historia", "Podręcznik"),
    ]
    assert len(client.get("/api/jobs").json()) == 4  # live defaults to false
    assert client.get(f"/api/jobs/{done.id}").json()["subject_name"] == "Biologia"
    assert client.get(f"/api/jobs/{queued.id}").json()["document_title"] == "Podręcznik"


def test_list_jobs_statement_count_does_not_grow(db, client):
    from test_reports_history import count_statements

    subject = make_subject(db)
    version, _ = make_version(db, subject.id, count=2, excluded=())

    def add(n: int) -> None:
        for i in range(n):
            document = version.id if i % 2 else None
            service.create_job(db, KIND, subject.id, {}, 2, document_version_id=document)

    add(1)
    one = count_statements(client, lambda: client.get("/api/jobs?live=true"))
    add(6)
    assert count_statements(client, lambda: client.get("/api/jobs?live=true")) == one
    assert len(client.get("/api/jobs?live=true").json()) == 7
