"""The monthly spending ceiling: calls, answers, follow-ups, rounds, jobs and voice."""

import json
import threading
from datetime import datetime

import pytest
from pydantic import BaseModel
from sqlalchemy import Engine, event, select
from test_adaptive_extension import finish, weak_round
from test_adaptive_support import build_world
from test_attempts_support import (
    ASSESS_TASK,
    FOLLOW_UP_TASK,
    act,
    ai_for,
    answer_current,
    answered_attempt,
    follow_up_for,
    override_providers,
    post_answer,
    result,
    start_round,
)
from test_costs_support import (
    NOW,
    ZONE,
    FakeEngine,
    PaidTextGenerator,
    durable,
    seed_usage,
    usage_rows,
)
from test_questions_support import QuestionFaker, all_good
from test_rounds_support import make_world
from test_topics_support import (
    fake_consolidation,
    fake_section_topics,
    make_subject,
    make_topic,
    make_version,
    passage_ids_in,
    upgrade,
)

from app.db.session import SessionLocal
from app.features.attempts.models import AttemptProcessing
from app.features.costs import estimates, spending
from app.features.costs.spending import (
    JOB_OVER_BUDGET,
    SPENDING_LIMIT,
    SpendingGuard,
    guard_for,
    money,
    reserved_usd,
)
from app.features.follow_ups.models import FollowUp
from app.features.jobs.models import AiJob
from app.features.providers.fakes import FakeTextGenerator, FakeTranscriber
from app.features.providers.registry import (
    RecordingTextGenerator,
    RecordingTranscriber,
    _generation_dep,
)
from app.features.providers.types import ProviderError
from app.features.recording import service as recordings
from app.features.recording.models import Recording
from app.features.rounds.models import Attempt, Round
from app.features.voice import service as voice

TEXT = "Wyjaśnij różnicę między PKB a PNB i podaj przykład."


class Out(BaseModel):
    x: int


@pytest.fixture
def url(settings):
    upgrade(settings.effective_database_url)
    return settings.effective_database_url


@pytest.fixture
def db(url):
    with SessionLocal(url) as session:
        yield session


@pytest.fixture
def world(db):
    return make_world(db)


@pytest.fixture(autouse=True)
def _no_reservations_left():
    yield
    spending._reserved.clear()


def set_ceiling(client, value: float) -> None:
    response = client.put("/api/settings", json={"spending_ceiling_usd": value})
    assert response.status_code == 200, response.text


def live_settings(client):
    return client.app.state.settings


class GuardedProviders:
    """The pipeline seam with fakes in the registry's recording adapters, guarded like the real
    ones by the app's current settings; `guarded` names the adapters that get the guard."""

    def __init__(self, client, url, guarded=("transcriber", "assessor", "follow_up")) -> None:
        self.client = client
        self.recorder = durable(url)
        self.guarded = guarded
        self.fake_transcriber = FakeTranscriber()
        self.fake_assessor = FakeTextGenerator({ASSESS_TASK: ai_for()})
        self.fake_follow_up = FakeTextGenerator(
            {FOLLOW_UP_TASK: lambda messages: follow_up_for(passage_ids_in(messages))}
        )

    def _guard(self, name):
        return guard_for(live_settings(self.client)) if name in self.guarded else None

    def transcriber(self):
        return RecordingTranscriber(
            lambda: self.fake_transcriber,
            "openai",
            "gpt-transcribe",
            self.recorder,
            guard=self._guard("transcriber"),
        )

    def assessor(self):
        return RecordingTextGenerator(
            lambda: self.fake_assessor,
            "openai",
            "gpt-6.1-sol",
            "assessment",
            self.recorder,
            guard=self._guard("assessor"),
        )

    def follow_up_generator(self):
        return RecordingTextGenerator(
            lambda: self.fake_follow_up,
            "openai",
            "gpt-6.1-sol",
            "generation",
            self.recorder,
            guard=self._guard("follow_up"),
        )


# ---- the setting ----


def test_setting_round_trip(client, settings):
    assert client.get("/api/settings").json()["spending_ceiling_usd"] == 0
    set_ceiling(client, 15)
    assert "AOC_SPENDING_CEILING_USD=15\n" in settings.user_env_file.read_text("utf-8")
    assert client.get("/api/settings").json()["spending_ceiling_usd"] == 15
    set_ceiling(client, 0)
    assert client.get("/api/settings").json()["spending_ceiling_usd"] == 0
    before = settings.user_env_file.read_text("utf-8")
    for bad in (-1, 20000):
        response = client.put("/api/settings", json={"spending_ceiling_usd": bad})
        assert response.status_code == 422, response.text
    assert settings.user_env_file.read_text("utf-8") == before


def test_no_ceiling_reads_nothing(settings, url):
    assert guard_for(settings) is None
    statements: list[str] = []

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    generator = RecordingTextGenerator(
        lambda: PaidTextGenerator({"t": Out(x=1)}),
        "openai",
        "gpt-6.1-sol",
        "generation",
        durable(url),
        guard=guard_for(settings),
    )
    event.listen(Engine, "before_cursor_execute", count)
    try:
        generator.generate_structured("t", "s", [], Out, operation_id="x")
    finally:
        event.remove(Engine, "before_cursor_execute", count)
    assert len(statements) == 1 and statements[0].lstrip().upper().startswith("INSERT")


# ---- the guard ----


def fixed_guard(url, ceiling):
    return SpendingGuard(url, ceiling, now=lambda: NOW, tz=ZONE)


def test_guard_blocks_over_ceiling(url, monkeypatch):
    seed_usage(url, 0.50, started_at=datetime(2026, 10, 1, 0, 30, tzinfo=ZONE))  # UTC: 30 Sep
    seed_usage(url, 0.48)
    seed_usage(url, 5.00, started_at=datetime(2026, 9, 30, 23, 30, tzinfo=ZONE))  # last month
    seed_usage(url, None, status="blocked", estimated_usd=3.0)  # blocked rows cost nothing
    monkeypatch.setattr(estimates, "text_call_usd", lambda *args: 0.05)
    fake = PaidTextGenerator({"t": Out(x=1)})
    generator = RecordingTextGenerator(
        lambda: fake, "openai", "gpt-6.1-sol", "generation", durable(url), guard=fixed_guard(url, 1)
    )
    with pytest.raises(ProviderError) as raised:
        generator.generate_structured("t", "s", [], Out, operation_id="questions.generate:9:0")
    error = raised.value
    assert (error.kind, error.retryable) == ("spending_limit", False)
    assert error.message == SPENDING_LIMIT.format(spent="$0.98", ceiling="$1.00")
    assert fake.calls == []
    blocked = usage_rows(url)[-1]
    assert (blocked.status, blocked.error_kind) == ("blocked", "spending_limit")
    assert (blocked.input_tokens, blocked.output_tokens, blocked.cost_usd) == (0, 0, None)
    assert blocked.estimated_usd == pytest.approx(0.05)
    assert reserved_usd(url) == 0
    # 0.98 + 0.02 fits exactly
    monkeypatch.setattr(estimates, "text_call_usd", lambda *args: 0.02)
    generator.generate_structured("t", "s", [], Out, operation_id="questions.generate:9:1")
    assert len(fake.calls) == 1


def test_in_flight_reservations_count(url, monkeypatch):
    monkeypatch.setattr(estimates, "text_call_usd", lambda *args: 0.06)
    fake = PaidTextGenerator({"t": Out(x=1)}, input_tokens=30_000, output_tokens=0)
    fake.gate = threading.Event()
    guard = fixed_guard(url, 0.10)

    def generator():
        return RecordingTextGenerator(
            lambda: fake, "openai", "gpt-6.1-sol", "generation", durable(url), guard=guard
        )

    first = threading.Thread(
        target=lambda: generator().generate_structured("t", "s", [], Out, operation_id="a")
    )
    first.start()
    for _ in range(500):  # the first call is in flight once its reservation is there
        if reserved_usd(url) > 0:
            break
        threading.Event().wait(0.01)
    assert reserved_usd(url) == pytest.approx(0.06)
    with pytest.raises(ProviderError) as raised:
        generator().generate_structured("t", "s", [], Out, operation_id="b")
    assert raised.value.kind == "spending_limit"
    fake.gate.set()
    first.join(5)
    assert reserved_usd(url) == 0
    assert [r.cost_usd for r in usage_rows(url) if r.status == "ok"] == [pytest.approx(0.06)]
    monkeypatch.setattr(estimates, "text_call_usd", lambda *args: 0.05)
    with pytest.raises(ProviderError):
        generator().generate_structured("t", "s", [], Out, operation_id="c")
    assert len(fake.calls) == 1


def test_reservation_released_when_the_call_fails(url, monkeypatch):
    monkeypatch.setattr(estimates, "text_call_usd", lambda *args: 0.05)
    error = ProviderError("server", "openai", "Provider failure: server.")
    generator = RecordingTextGenerator(
        lambda: FakeTextGenerator({}, error=error),
        "openai",
        "gpt-6.1-sol",
        "generation",
        durable(url),
        guard=fixed_guard(url, 1),
    )
    with pytest.raises(ProviderError):
        generator.generate_structured("t", "s", [], Out, operation_id="x")
    assert reserved_usd(url) == 0
    assert usage_rows(url)[-1].status == "error"


# ---- answers and follow-ups ----


def test_blocked_answer_keeps_its_recording(client, world, url, settings, db):
    providers = GuardedProviders(client, url)
    override_providers(client.app, providers)
    _, attempt_id = answered_attempt(client, world)
    set_ceiling(client, 1)
    seed_usage(url, 1.0, started_at=datetime.now(ZONE))
    events, audio = post_answer(client, attempt_id)
    assert events.status_code == 200 and audio.status_code == 201
    failed = result(client, attempt_id)
    assert failed["status"] == "failed"
    db.expire_all()
    row = db.scalar(select(AttemptProcessing).where(AttemptProcessing.attempt_id == attempt_id))
    assert (row.failure_kind, row.failure_message) == (
        "spending_limit",
        SPENDING_LIMIT.format(spent="$1.00", ceiling="$1.00"),
    )
    assert db.get(Attempt, attempt_id).outcome == "technical_failure"
    recording = db.get(Recording, db.get(Attempt, attempt_id).recording_id)
    assert recordings.audio_path(live_settings(client), recording).is_file()
    assert providers.fake_transcriber.calls == []
    assert [r.status for r in usage_rows(url) if r.operation == "attempt.transcribe"] == ["blocked"]

    set_ceiling(client, 5)
    response = client.post(f"/api/attempts/{attempt_id}/retry")
    assert response.status_code == 202, response.text
    assert result(client, attempt_id)["status"] == "ready"
    assert len(providers.fake_transcriber.calls) == 1


def test_blocked_follow_up_shows_the_reason(client, world, url, db):
    providers = GuardedProviders(client, url, guarded=("follow_up",))
    override_providers(client.app, providers)
    rid = start_round(client, world, "practice", 2, follow_ups={"enabled": True})["id"]
    main = answer_current(client, rid)
    set_ceiling(client, 1)
    seed_usage(url, 1.0, started_at=datetime.now(ZONE))
    post_answer(client, main)
    assert result(client, main)["status"] == "ready"
    state = client.get(f"/api/rounds/{rid}").json()
    # the main answer's calls count too: the fakes report no usage, so at their estimates
    _, start, end = spending.month_bounds()
    spent = spending.spent_between(db, start, end)
    assert spent > 1.0
    message = SPENDING_LIMIT.format(spent=money(spent), ceiling="$1.00")
    assert state["follow_up"]["status"] == "failed"
    assert state["follow_up"]["failure"] == {"kind": "spending_limit", "message": message}
    assert providers.fake_follow_up.calls == []
    db.expire_all()
    assert db.scalar(select(FollowUp)).failure_message == message
    # the round goes on without it: the main answer's feedback
    state = act(client, rid, "no_follow_up", "processing")
    assert (state["phase"], state["current_attempt"]["id"]) == ("feedback", main)


# ---- rounds ----


def round_body(world, count=4):
    return {
        "subject_id": world["subject"].id,
        "config": {
            "scope": {"kind": "subject"},
            "question_count": count,
            "answer_seconds": 120,
            "follow_ups": {"enabled": True},
        },
    }


def high_estimate(client, db, world, count=4) -> float:
    return estimates.round_estimate(
        db,
        live_settings(client),
        subject_id=world["subject"].id,
        question_count=count,
        answer_seconds=120,
        follow_ups=True,
    ).high_usd


def test_round_start_checks_the_high_estimate(client, world, url, db):
    set_ceiling(client, 1)
    high = high_estimate(client, db, world)
    assert 0 < high < 1
    seed_usage(url, 1 - high + 0.01, started_at=datetime.now(ZONE))
    response = client.post("/api/rounds", json=round_body(world))
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    estimate = detail["estimate"]
    assert estimate["high_usd"] == pytest.approx(high) and estimate["fits"] is False
    assert detail["message"] == spending.ROUND_OVER_BUDGET.format(
        low=money(estimate["low_usd"]),
        high=money(estimate["high_usd"]).removeprefix("$"),
        left=money(estimate["month"]["remaining_usd"]),
        ceiling="$1.00",
    )
    assert db.scalar(select(Round)) is None
    set_ceiling(client, 2)
    assert client.post("/api/rounds", json=round_body(world)).status_code == 201


def statements_of(call) -> int:
    found: list[str] = []

    def count(conn, cursor, statement, *args):
        found.append(statement)

    event.listen(Engine, "before_cursor_execute", count)
    try:
        call()
    finally:
        event.remove(Engine, "before_cursor_execute", count)
    return len(found)


def test_no_ceiling_round_start_unchanged(client, world, monkeypatch):
    client.post("/api/rounds", json=round_body(world))  # the same history before both counts
    plain = statements_of(lambda: client.post("/api/rounds", json=round_body(world)))

    def never(*args, **kwargs):
        raise AssertionError("no estimate without a ceiling")

    monkeypatch.setattr(estimates, "round_estimate", never)
    monkeypatch.setattr(spending, "month_state", never)
    response = None

    def start():
        nonlocal response
        response = client.post("/api/rounds", json=round_body(world))

    assert statements_of(start) == plain
    assert response.status_code == 201, response.text


def test_retry_round_checks_the_estimate(client, db, url):
    world = build_world(db, per_objective=(3, 3, 3))
    body = {
        "subject_id": world.subject.id,
        "config": {"scope": {"kind": "subject"}, "question_count": 2, "retry_round": True},
    }
    rid = client.post("/api/rounds", json=body).json()["id"]
    finish(db, rid, [{"band": "weak"}, {"band": "weak"}])
    assert client.get(f"/api/rounds/{rid}/retry").json()["status"] == "ready"
    set_ceiling(client, 1)
    seed_usage(url, 0.999, started_at=datetime.now(ZONE))
    response = client.post(f"/api/rounds/{rid}/retry")
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["estimate"]["fits"] is False
    assert db.scalar(select(Round).where(Round.round_type == "retry")) is None
    set_ceiling(client, 5)
    assert client.post(f"/api/rounds/{rid}/retry").status_code == 201


# ---- jobs ----


class Generators:
    """The generation dependency: a paid fake behind the guard of the app's current settings."""

    def __init__(self, client, url, fake) -> None:
        self.client, self.url, self.fake = client, url, fake

    def __call__(self):
        return RecordingTextGenerator(
            lambda: self.fake,
            "openai",
            "gpt-6.1-sol",
            "generation",
            durable(self.url),
            guard=guard_for(live_settings(self.client)),
        )


def extraction_fake():
    return PaidTextGenerator(
        {"topics.extract_section": fake_section_topics, "topics.consolidate": fake_consolidation},
        input_tokens=100,
        output_tokens=10,
    )


def test_job_refused_and_resumable(client, db, url):
    subject = make_subject(db)
    version, passages = make_version(db, subject.id, count=10, excluded=())
    make_topic(db, subject.id, "Ateny", passages[:2], ordinal=0)
    fake = extraction_fake()
    client.app.dependency_overrides[_generation_dep] = Generators(client, url, fake)
    set_ceiling(client, 1)
    seed_usage(url, 1.0, started_at=datetime.now(ZONE))

    response = client.post("/api/topics/extract", json={"document_version_id": version.id})
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["estimate"]["kind"] == "topics.extract" and detail["estimate"]["fits"] is False
    job = db.scalar(select(AiJob).order_by(AiJob.id.desc()))
    db.refresh(job)
    assert job.status == "failed"
    assert job.error_message == detail["message"]
    assert detail["message"] == JOB_OVER_BUDGET.format(
        usd=money(detail["estimate"]["usd"]), left="$0.00", ceiling="$1.00"
    )
    out = client.get(f"/api/jobs/{job.id}").json()
    assert out["resumable"] is True and out["status"] == "failed"
    assert job.id not in [j["id"] for j in client.get("/api/jobs?live=true").json()]
    assert fake.calls == []

    set_ceiling(client, 5)
    response = client.post(f"/api/jobs/{job.id}/resume")
    assert response.status_code == 202, response.text
    db.refresh(job)
    assert job.status == "done" and fake.calls

    # question generation: refused the same way
    set_ceiling(client, 1)
    calls = len(fake.calls)
    response = client.post(
        "/api/questions/generate", json={"subject_id": subject.id, "per_topic": 2}
    )
    assert response.status_code == 409, response.text
    job = db.scalar(select(AiJob).order_by(AiJob.id.desc()))
    db.refresh(job)
    assert (job.kind, job.status) == ("questions.generate", "failed")
    assert len(fake.calls) == calls


def test_extension_generation_refused(client, db, url):
    world = build_world(db, per_objective=(1, 1, 1))
    rid = weak_round(client, db, world)
    assert client.get(f"/api/rounds/{rid}/extension").json()["status"] == "no_questions"
    fake = PaidTextGenerator({"questions.generate": QuestionFaker(), "questions.verify": all_good})
    client.app.dependency_overrides[_generation_dep] = Generators(client, url, fake)
    set_ceiling(client, 1)
    seed_usage(url, 1.0, started_at=datetime.now(ZONE))
    response = client.post(f"/api/rounds/{rid}/extension/generate")
    assert response.status_code == 409, response.text
    job = db.scalar(select(AiJob).order_by(AiJob.id.desc()))
    db.refresh(job)
    assert (job.kind, job.status) == ("questions.generate", "failed")
    assert client.get(f"/api/jobs/{job.id}").json()["resumable"] is True
    assert fake.calls == []
    set_ceiling(client, 5)
    assert client.post(f"/api/jobs/{job.id}/resume").status_code == 202
    db.refresh(job)
    assert job.status == "done" and fake.calls


def test_mid_job_block_fails_the_job(client, db, url, monkeypatch):
    subject = make_subject(db)
    version, _ = make_version(db, subject.id, count=40, excluded=())
    set_ceiling(client, 1)
    calls: list[int] = []

    def on_call(messages):
        calls.append(1)
        if len(calls) == 2:  # after the second section someone else spends the rest
            seed_usage(url, 0.999, started_at=datetime.now(ZONE))
        return fake_section_topics(messages)

    fake = PaidTextGenerator(
        {"topics.extract_section": on_call, "topics.consolidate": fake_consolidation},
        input_tokens=10,
        output_tokens=1,
    )
    monkeypatch.setattr("app.features.topics.service.SECTION_CHAR_LIMIT", 120)
    client.app.dependency_overrides[_generation_dep] = Generators(client, url, fake)
    response = client.post("/api/topics/extract", json={"document_version_id": version.id})
    assert response.status_code == 202, response.text
    job = db.scalar(select(AiJob).order_by(AiJob.id.desc()))
    db.refresh(job)
    assert job.steps_total > 3
    assert job.status == "failed"
    assert job.error_message.startswith("The monthly spending ceiling is reached")
    assert job.steps_done == 2 and len(fake.calls) == 2

    set_ceiling(client, 5)
    assert client.post(f"/api/jobs/{job.id}/resume").status_code == 202
    db.refresh(job)
    assert job.status == "done"
    assert len(fake.calls) == job.steps_total  # only the missing steps ran


def test_resume_prices_only_the_steps_left(client, db, url, monkeypatch):
    """A resume is refused only when the steps not yet done pass what is left, not the job."""
    from app.features.jobs.service import load_params
    from app.features.topics.service import start_extraction

    subject = make_subject(db)
    version, _ = make_version(db, subject.id, count=40, excluded=())
    monkeypatch.setattr("app.features.topics.service.SECTION_CHAR_LIMIT", 120)
    job = start_extraction(db, version.id)
    sections = len(load_params(job)["sections"])
    assert sections > 3
    # every section but the last is done; the last section and the consolidation are left
    job.state_json = json.dumps({"steps": {str(i): {"topics": []} for i in range(sections - 1)}})
    job.steps_done = sections - 1
    job.status = "failed"
    db.commit()
    settings = live_settings(client)
    whole = estimates.estimate_job(db, settings, job.kind, load_params(job)).usd
    done = frozenset(range(sections - 1))
    left = estimates.estimate_job(db, settings, job.kind, load_params(job), done=done).usd
    assert 0 < left < whole
    set_ceiling(client, 1)
    seed_usage(url, 1 - (left + whole) / 2, started_at=datetime.now(ZONE))
    fake = extraction_fake()
    client.app.dependency_overrides[_generation_dep] = Generators(client, url, fake)

    response = client.post(f"/api/jobs/{job.id}/resume")
    assert response.status_code == 202, response.text
    db.refresh(job)
    assert job.status == "done", job.error_message
    assert len(fake.calls) == 2  # the last section and the consolidation


# ---- voice ----


def test_voice_blocked(client, url, monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(voice, "get_synthesizer", lambda settings, provider: engine)
    keys = live_settings(client).user_env_file
    keys.parent.mkdir(parents=True, exist_ok=True)
    keys.write_text(
        "AOC_AZURE_SPEECH_KEY=" + "k" * 32 + "\nAOC_AZURE_SPEECH_REGION=westeurope\n", "utf-8"
    )
    set_ceiling(client, 1)
    seed_usage(url, 1.0, started_at=datetime.now(ZONE))
    response = client.post("/api/voice/speak", json={"text": TEXT, "provider": "azure_speech"})
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == SPENDING_LIMIT.format(spent="$1.00", ceiling="$1.00")
    assert engine.calls == []
    [blocked] = [r for r in usage_rows(url) if r.operation == "voice.synthesize"]
    assert (blocked.status, blocked.provider) == ("blocked", "azure_speech")
