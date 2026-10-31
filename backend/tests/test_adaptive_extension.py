import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from test_adaptive_support import answer_item, build_world
from test_coverage_support import make_answer, make_objective_question, make_round
from test_questions_support import QuestionFaker, all_good
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.coverage.service import subject_coverage
from app.features.jobs import service as jobs
from app.features.jobs.models import AiJob
from app.features.providers.fakes import FakeTextGenerator
from app.features.providers.registry import _generation_dep
from app.features.questions.service import GENERATE_KIND, OTHER_POLICY_RUNNING
from app.features.rounds import extension
from app.features.rounds.extension import EXTENSION_NOT_NOW
from app.features.rounds.models import Attempt, Round, RoundItem

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def start_round(client, world, count, *, ext=3, enabled=True, **config) -> Round:
    config.setdefault("scope", {"kind": "subject"})
    config.setdefault("style", "short")
    body = {
        "subject_id": world.subject.id,
        "config": {
            "question_count": count,
            "extension": {"enabled": enabled, "question_count": ext},
            **config,
        },
    }
    response = client.post("/api/rounds", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def items_of(db, round_id: int) -> list[RoundItem]:
    return list(
        db.query(RoundItem).filter_by(round_id=round_id, role="main").order_by(RoundItem.ordinal)
    )


def finish(db, round_id: int, answers: list[dict], status: str = "finished") -> Round:
    """Answers the items in order (one dict of answer_item kwargs each), then closes the round."""
    for item, answer in zip(items_of(db, round_id), answers, strict=False):
        answer_item(db, item, at=NOW, **answer)
    db.expire_all()
    row = db.get(Round, round_id)
    row.status = status
    row.phase = "summary"
    row.finished_at = NOW
    row.version += 1
    db.commit()
    return row


def objective_index(world, item: RoundItem) -> int:
    return world.objective_of(item.question_version_id)


def answers_by_objective(db, world, round_id, bands: dict[int, dict]) -> list[dict]:
    return [bands[objective_index(world, item)] for item in items_of(db, round_id)]


WEAK = {"band": "weak"}
GOOD = {"band": "good"}
VERY_GOOD = {"band": "very_good"}


def get_offer(client, round_id: int) -> dict:
    response = client.get(f"/api/rounds/{round_id}/extension")
    assert response.status_code == 200, response.text
    return response.json()


def weak_round(client, db, world, *, count=3, ext=3, bands=None, **config) -> int:
    """A finished round, one item per topic: topic 0 Weak, the others Good (by default)."""
    round_id = start_round(client, world, count, ext=ext, **config)
    bands = bands or {0: WEAK, 1: GOOD, 2: GOOD}
    finish(db, round_id, answers_by_objective(db, world, round_id, bands))
    return round_id


def test_offer_statuses(client, db):
    world = build_world(db)
    off = start_round(client, world, 3, enabled=False)
    finish(db, off, [WEAK, WEAK, WEAK])
    assert get_offer(client, off)["status"] == "off"

    active = start_round(client, world, 3)
    assert get_offer(client, active)["status"] == "not_finished"
    db.get(Round, active).status = "paused"
    db.commit()
    assert get_offer(client, active)["status"] == "not_finished"

    ended = start_round(client, world, 3)
    finish(db, ended, [WEAK], status="abandoned")
    assert get_offer(client, ended)["status"] == "ended_early"

    good = start_round(client, world, 3)
    finish(db, good, [GOOD, VERY_GOOD, GOOD])
    assert get_offer(client, good)["status"] == "no_weak"

    waiting = start_round(client, world, 3)
    real_now = datetime.now(UTC)
    finish(db, waiting, [WEAK, GOOD, {"processing": "processing"}])
    last = items_of(db, waiting)[-1]
    attempt = db.query(Attempt).filter_by(round_item_id=last.id).one()
    attempt.ended_at = real_now  # just stopped: its audio may still arrive
    db.commit()
    assert get_offer(client, waiting)["status"] == "waiting"
    answer_item(db, last, at=NOW, band="good")
    offer = get_offer(client, waiting)
    assert offer["status"] == "ready"
    assert offer["requested"] == 3

    overdue = start_round(client, world, 3)
    finish(db, overdue, [WEAK, GOOD, {"processing": "processing"}])
    last = items_of(db, overdue)[-1]
    attempt = db.query(Attempt).filter_by(round_item_id=last.id).one()
    attempt.ended_at = real_now - timedelta(minutes=10)
    db.commit()
    assert get_offer(client, overdue)["status"] == "ready"
    db.expire_all()
    assert db.get(Attempt, attempt.id).processing_status == "processing"  # the GET did not settle

    started = client.post(f"/api/rounds/{overdue}/extension")
    assert started.status_code == 201, started.text
    offer = get_offer(client, overdue)
    assert offer["status"] == "started"
    assert offer["extension_round_id"] == started.json()["id"]
    assert offer["extension_status"] == "active"
    assert get_offer(client, started.json()["id"])["status"] == "off"  # an extension has none


def test_offer_weak_from_stored_band(client, db):
    world = build_world(db, per_objective=(6, 6, 6))
    round_id = start_round(client, world, 6, ext=10)
    finish(
        db,
        round_id,
        [
            {"band": "good", "factual": 55.0},  # stored Good: not weak, whatever the table now
            {"outcome": "dont_know"},
            {"outcome": "skipped"},
            {"band": None, "assessment_status": "no_speech"},
            {"outcome": "technical_failure"},
            {"band": "good"},
        ],
    )
    _, targets, families = extension._main_answers(db, db.get(Round, round_id))
    assert sorted(t.ordinal for t in targets) == [1, 2]
    assert {t.factual for t in targets} == {0.0}
    assert len(families) == 6

    calm = start_round(client, world, 3)
    finish(
        db,
        calm,
        [
            {"band": "good", "factual": 55.0},
            {"band": None, "assessment_status": "no_speech"},
            {"outcome": "technical_failure"},
        ],
    )
    assert get_offer(client, calm)["status"] == "no_weak"


def test_offer_topics_and_available(client, db):
    world = build_world(db)
    # outside the parent's style: never offered
    make_objective_question(
        db, world.subject.id, world.topics[2].id, world.objectives[2].id, "Pytanie otwarte?"
    )
    round_id = weak_round(
        client,
        db,
        world,
        ext=10,
        bands={0: {"band": "weak"}, 1: GOOD, 2: {"band": "needs_improvement"}},
    )
    offer = get_offer(client, round_id)
    assert offer["status"] == "ready"
    assert [t["title"] for t in offer["topics"]] == [
        "Ostracyzm: procedura i cel",
        "Reformy Klejstenesa",
    ]
    assert [t["topic_id"] for t in offer["topics"]] == [world.topics[0].id, world.topics[2].id]
    assert offer["available"] == 6  # 3 + 3 new: the parent's own questions are left out


def test_start_extension(client, db):
    world = build_world(db)
    round_id = weak_round(client, db, world, feedback_timing="exam", prep_seconds=7)
    parent = db.get(Round, round_id)
    response = client.post(f"/api/rounds/{round_id}/extension")
    assert response.status_code == 201, response.text
    state = response.json()
    child = db.get(Round, state["id"])
    assert (child.round_type, child.parent_round_id) == ("extension", round_id)
    config = json.loads(child.config_json)
    expected = json.loads(parent.config_json)
    expected.update(round_type="extension", question_count=3)
    expected["follow_ups"]["enabled"] = False
    expected["extension"]["enabled"] = False
    assert config == expected
    assert state["total"] == 3
    items = items_of(db, child.id)
    assert {i.role for i in items} == {"main"}
    assert db.query(Attempt).filter_by(round_item_id=items[0].id).one().attempt_kind == "initial"
    parent_versions = {i.question_version_id for i in items_of(db, round_id)}
    assert not parent_versions & {i.question_version_id for i in items}
    assert {objective_index(world, i) for i in items} == {0}  # the weak topic only
    assert get_offer(client, round_id)["status"] == "started"


def test_start_takes_what_is_available(client, db):
    world = build_world(db, per_objective=(2, 4, 4))
    round_id = weak_round(client, db, world, ext=3)
    offer = get_offer(client, round_id)
    assert (offer["status"], offer["available"]) == ("ready", 1)
    state = client.post(f"/api/rounds/{round_id}/extension").json()
    assert state["total"] == 1
    assert state["config"]["question_count"] == 1


def test_start_twice_returns_same_round(client, db, monkeypatch):
    world = build_world(db)
    round_id = weak_round(client, db, world)
    first = client.post(f"/api/rounds/{round_id}/extension")
    second = client.post(f"/api/rounds/{round_id}/extension")
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert db.query(Round).filter_by(parent_round_id=round_id).count() == 1

    # A parallel start that read the offer before the other one committed.
    other = weak_round(client, db, world)
    created = client.post(f"/api/rounds/{other}/extension").json()
    original_read, original_child = extension._read, extension._child

    def stale_read(session, rid):
        monkeypatch.setattr(extension, "_child", lambda s, r: None)
        try:
            return original_read(session, rid)
        finally:
            monkeypatch.setattr(extension, "_child", original_child)

    monkeypatch.setattr(extension, "_read", stale_read)
    raced = client.post(f"/api/rounds/{other}/extension")
    assert raced.status_code == 200, raced.text
    assert raced.json()["id"] == created["id"]
    assert db.query(Round).filter_by(parent_round_id=other).count() == 1


def test_start_not_available_is_409(client, db):
    world = build_world(db)
    good = start_round(client, world, 3)
    finish(db, good, [GOOD, GOOD, GOOD])
    active = start_round(client, world, 3)
    ended = start_round(client, world, 3)
    finish(db, ended, [WEAK], status="abandoned")
    off = start_round(client, world, 3, enabled=False)
    finish(db, off, [WEAK, WEAK, WEAK])
    for round_id in (good, active, ended, off):
        response = client.post(f"/api/rounds/{round_id}/extension")
        assert response.status_code == 409
        assert response.json()["detail"] == EXTENSION_NOT_NOW
    assert db.query(Round).filter(Round.parent_round_id.is_not(None)).count() == 0


@pytest.fixture
def generator(client):
    fake = FakeTextGenerator({"questions.generate": QuestionFaker(), "questions.verify": all_good})
    client.app.dependency_overrides[_generation_dep] = lambda: fake
    return fake


def test_generation_job_params(client, db, generator):
    world = build_world(db, per_objective=(1, 1, 1))
    round_id = weak_round(client, db, world, difficulty="recall")
    offer = get_offer(client, round_id)
    assert (offer["status"], offer["available"]) == ("no_questions", 0)
    response = client.post(f"/api/rounds/{round_id}/extension/generate")
    assert response.status_code == 202, response.text
    job = db.get(AiJob, response.json()["job_id"])
    params = json.loads(job.params_json)
    assert {b["topic_id"] for b in params["batches"]} == {world.topics[0].id}
    assert (params["per_topic"], params["style"], params["difficulty"]) == (3, "short", "recall")
    db.expire_all()
    offer = get_offer(client, round_id)
    assert (offer["status"], offer["available"]) == ("ready", 3)
    again = client.post(f"/api/rounds/{round_id}/extension/generate")
    assert again.status_code == 409
    assert again.json()["detail"] == EXTENSION_NOT_NOW

    second = build_world(db, name="Biologia", per_objective=(1, 1, 1))
    other = weak_round(client, db, second, bands={0: GOOD, 1: WEAK, 2: GOOD})
    assert get_offer(client, other)["status"] == "no_questions"
    jobs.create_job(db, GENERATE_KIND, second.subject.id, {"batches": []}, steps_total=1)
    offer = get_offer(client, other)
    assert offer["status"] == "generating"
    assert offer["job_id"] is not None


def test_generation_with_other_policy_job_running_is_409(client, db, generator):
    """A live expanded batch reads as generating; asking for a strict one gets its message."""
    world = build_world(db, per_objective=(1, 1, 1))
    round_id = weak_round(client, db, world)
    live = jobs.create_job(
        db, GENERATE_KIND, world.subject.id, {"source_policy": "expanded"}, steps_total=1
    )
    offer = get_offer(client, round_id)
    assert (offer["status"], offer["job_id"]) == ("generating", live.id)
    response = client.post(f"/api/rounds/{round_id}/extension/generate")
    assert response.status_code == 409
    assert response.json()["detail"] == OTHER_POLICY_RUNNING
    assert generator.calls == [] and db.query(AiJob).count() == 1


def test_needs_review_questions_are_named(client, db):
    world = build_world(db, per_objective=(1, 1, 1))
    make_objective_question(
        db,
        world.subject.id,
        world.topics[0].id,
        world.objectives[0].id,
        "Pytanie do przeglądu?",
        status="needs_review",
    )
    round_id = weak_round(client, db, world)
    offer = get_offer(client, round_id)
    assert (offer["status"], offer["needs_review"]) == ("no_questions", 1)


def _report(client, round_id: int) -> dict:
    response = client.get(f"/api/reports/rounds/{round_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _history(client, subject_id: int) -> list[dict]:
    return client.get("/api/reports/history", params={"subject_id": subject_id}).json()


def test_extension_never_changes_primary_score(client, db):
    world = build_world(db)
    round_id = weak_round(client, db, world)
    before = _report(client, round_id)
    history_before = _history(client, world.subject.id)
    child = client.post(f"/api/rounds/{round_id}/extension").json()["id"]
    listed = [r["id"] for r in client.get(f"/api/rounds?subject_id={world.subject.id}").json()]
    assert child in listed  # an open extension can be resumed from Home
    finish(db, child, [VERY_GOOD, VERY_GOOD, VERY_GOOD])
    after = _report(client, round_id)
    for key in ("knowledge", "delivery", "items", "provisional", "asked", "total"):
        assert after[key] == before[key], key
    assert _history(client, world.subject.id) == history_before
    listed = client.get(f"/api/rounds?subject_id={world.subject.id}").json()
    assert child not in [r["id"] for r in listed]
    assert {r["round_type"] for r in listed} == {"standard"}


def test_extension_series(client, db):
    world = build_world(db)
    round_id = weak_round(client, db, world, ext=1, feedback_timing="exam")
    assert _report(client, round_id)["extension"] is None
    child = client.post(f"/api/rounds/{round_id}/extension").json()["id"]
    running = _report(client, round_id)["extension"]
    assert (running["round_id"], running["status"], running["withheld"]) == (child, "active", True)
    assert running["knowledge"] is None
    finish(db, child, [VERY_GOOD])
    series = _report(client, round_id)["extension"]
    own = _report(client, child)
    assert (series["asked"], series["total"], series["good_or_better"]) == (1, 1, 1)
    assert series["withheld"] is False
    assert series["knowledge"]["mean_factual"] == own["knowledge"]["mean_factual"] == 88.0
    assert own["parent_round_id"] == round_id
    assert own["knowledge"]["primary_score"] == 88.0
    assert own["extension"] is None


def _line(coverage, objective_id: int):
    return next(
        line
        for topic in coverage.topics
        for line in topic.objectives
        if line.objective_id == objective_id
    )


def test_extension_answers_count_as_coverage_evidence(client, db):
    world = build_world(db)
    round_id = weak_round(client, db, world, ext=1)
    before = _line(subject_coverage(db, world.subject.id), world.objectives[0].id)
    child = client.post(f"/api/rounds/{round_id}/extension").json()["id"]
    finish(db, child, [VERY_GOOD])
    db.expire_all()
    after = _line(subject_coverage(db, world.subject.id), world.objectives[0].id)
    assert after.answers == before.answers + 1
    assert after.independent_answers == before.independent_answers + 1


def _statements(client, call) -> list[str]:
    seen: list[str] = []

    def listener(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", listener)
    try:
        call()
    finally:
        event.remove(Engine, "before_cursor_execute", listener)
    return seen


def test_offer_query_budget_and_read_only(client, db):
    world = build_world(db, per_objective=(8, 8, 8))
    small = start_round(client, world, 2)
    finish(db, small, [WEAK, GOOD])
    large = start_round(client, world, 8)
    finish(db, large, [WEAK, GOOD, GOOD, GOOD, GOOD, GOOD, GOOD, GOOD])

    def budget(round_id: int) -> list[str]:
        statements = _statements(client, lambda: get_offer(client, round_id))
        assert get_offer(client, round_id)["status"] == "ready"
        return statements

    two, eight = budget(small), budget(large)
    assert len(two) == len(eight) <= 16
    for n in range(5):
        at = NOW - timedelta(days=3 + n)
        past = make_round(db, world.subject.id, at=at)
        make_answer(db, past, world.questions[1][7], at=at, band="good")
    assert len(budget(small)) == len(two)
    writes = [
        s for s in two + eight if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
    assert writes == []
