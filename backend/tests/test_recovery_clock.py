"""The round state carries the server's clock for the phase, read without any write."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import Engine, event
from test_attempts_support import (
    FakeProviders,
    act,
    answer_current,
    override_providers,
    post_answer,
    start_round,
)
from test_reports_history import count_statements
from test_rounds_support import Clock, make_world
from test_topics_support import make_question, upgrade

from app.db.session import SessionLocal
from app.features.rounds import service
from app.features.rounds.models import Attempt


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    return make_world(db)


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(service, "now", clock)
    return clock


def same_instant(text: str | None, value) -> bool:
    assert text is not None and (text.endswith("Z") or text.endswith("+00:00")), text
    return datetime.fromisoformat(text.replace("Z", "+00:00")) == value


def clock_of(client, rid: int) -> dict:
    return client.get(f"/api/rounds/{rid}").json()["clock"]


def test_preparation_clock(client, world, clock):
    rid = start_round(client, world, prep_seconds=20, answer_seconds=40)["id"]
    t0 = clock.value
    act(client, rid, "question_ready", "question")
    clock.advance(seconds=4)
    body = clock_of(client, rid)
    assert same_instant(body["server_now"], t0 + timedelta(seconds=4))
    assert same_instant(body["phase_started_at"], t0)
    assert same_instant(body["prep_deadline_at"], t0 + timedelta(seconds=20))
    assert body["answer_deadline_at"] is None and body["paused_at"] is None


def test_recording_clock_uses_the_start_answer_event(client, world, clock):
    rid = start_round(client, world, prep_seconds=20, answer_seconds=40)["id"]
    act(client, rid, "question_ready", "question")
    clock.advance(seconds=6)
    started = clock.value
    act(client, rid, "start_answer", "preparation", start_reason="button")
    clock.advance(seconds=3)
    body = clock_of(client, rid)
    assert same_instant(body["phase_started_at"], started)
    assert same_instant(body["answer_deadline_at"], started + timedelta(seconds=40))
    assert body["prep_deadline_at"] is None


def test_follow_up_part_uses_follow_up_timers(client, world, clock):
    override_providers(client.app, FakeProviders())
    rid = start_round(
        client,
        world,
        "practice",
        2,
        prep_seconds=20,
        answer_seconds=40,
        follow_ups={"enabled": True, "prep_seconds": 7, "answer_seconds": 25},
    )["id"]
    post_answer(client, answer_current(client, rid))
    assert client.get(f"/api/rounds/{rid}").json()["part"] == "followup"
    clock.advance(seconds=30)
    t1 = clock.value
    act(client, rid, "question_ready", "question")
    body = clock_of(client, rid)
    assert same_instant(body["prep_deadline_at"], t1 + timedelta(seconds=7))
    clock.advance(seconds=2)
    act(client, rid, "start_answer", "preparation", start_reason="button")
    body = clock_of(client, rid)
    assert same_instant(body["answer_deadline_at"], t1 + timedelta(seconds=27))


def test_paused_clock(client, world, clock):
    rid = start_round(client, world)["id"]
    act(client, rid, "question_ready", "question")
    clock.advance(seconds=5)
    paused = clock.value
    act(client, rid, "pause", "preparation")
    clock.advance(seconds=60)
    body = clock_of(client, rid)
    assert same_instant(body["paused_at"], paused)
    assert body["prep_deadline_at"] is None and body["answer_deadline_at"] is None
    assert body["phase_started_at"] is None


def writes_and_count(client, rid: int) -> tuple[int, int]:
    seen: list[str] = []

    def listener(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", listener)
    try:
        assert client.get(f"/api/rounds/{rid}").status_code == 200
    finally:
        event.remove(Engine, "before_cursor_execute", listener)
    writes = [s for s in seen if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    return len(seen), len(writes)


def paused_round(client, world, pauses: int, count: int = 2) -> int:
    rid = start_round(client, world, count=count)["id"]
    for _ in range(pauses):
        act(client, rid, "pause", "question")
        act(client, rid, "resume", "question")
    act(client, rid, "question_ready", "question")
    return rid


def test_get_round_budget_and_read_only(client, world, db, clock):
    athens = world["topics"][0]
    for n in range(4):  # 8 + 4 questions: enough for a round of 8 within the subject
        make_question(db, world["subject"].id, athens.id, f"Pytanie dodatkowe {n}.")
    few = paused_round(client, world, 1)
    many = paused_round(client, world, 6)
    assert writes_and_count(client, few) == writes_and_count(client, many)
    assert writes_and_count(client, few)[1] == 0
    small = paused_round(client, world, 1, count=2)
    large = paused_round(client, world, 1, count=8)
    assert writes_and_count(client, small) == writes_and_count(client, large)
    assert (
        count_statements(client, lambda: client.get(f"/api/rounds/{small}"))
        == (writes_and_count(client, small)[0])
    )

    # an overdue missing-audio answer stays unsettled: GET /api/rounds/{id} writes nothing
    rid = start_round(client, world, "practice", 1)["id"]
    attempt_id = answer_current(client, rid)
    attempt = db.get(Attempt, attempt_id)
    attempt.ended_at = clock.value - timedelta(seconds=300)
    db.commit()
    assert writes_and_count(client, rid)[1] == 0
    db.expire_all()
    assert db.get(Attempt, attempt_id).processing_status == "processing"
