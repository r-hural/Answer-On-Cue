"""Follow-ups through the API: the attempt pipeline generates them (fakes behind the seam)."""

import json

import pytest
from sqlalchemy import select
from test_attempts_support import (
    FOLLOW_UP_TASK,
    FOLLOW_UP_TEXT,
    FakeProviders,
    act,
    answer_current,
    follow_up_for,
    override_providers,
    post_answer,
    start_round,
)
from test_rounds_support import make_world
from test_topics_support import passage_ids_in, upgrade

from app.db.session import SessionLocal
from app.features.attempts import pipeline
from app.features.follow_ups.models import FollowUp
from app.features.providers.fakes import FakeTextGenerator, FakeTranscriber
from app.features.providers.types import ProviderError
from app.features.questions.models import QuestionVersion, RubricVersion
from app.features.rounds.models import Attempt, RoundItem

ON = {"enabled": True}


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    return make_world(db)


def use(client, **fakes) -> FakeProviders:
    fake = FakeProviders(**fakes)
    override_providers(client.app, fake)
    return fake


def state_of(client, rid: int) -> dict:
    return client.get(f"/api/rounds/{rid}").json()


def answered(client, world, timing="practice", count=2, audio="speech_mid.wav", **config):
    config.setdefault("follow_ups", ON)
    rid = start_round(client, world, timing, count, **config)["id"]
    main = answer_current(client, rid)
    post_answer(client, main, audio)
    return rid, main


def test_practice_answer_gets_follow_up_before_feedback(client, db, world):
    fake = use(client)
    rid, main = answered(client, world)
    state = state_of(client, rid)
    assert (state["part"], state["phase"], state["status"]) == ("followup", "question", "active")
    assert state["current"]["text"] == FOLLOW_UP_TEXT
    assert state["current_attempt"]["outcome"] == "pending"
    db.expire_all()
    row = db.scalar(select(FollowUp))
    assert (row.status, row.target, row.tries, row.main_attempt_id) == (
        "ready",
        "justification",
        1,
        main,
    )
    provenance = json.loads(row.provenance_json)
    assert provenance["prompt_version"] == "follow-up-v3"
    assert provenance["operation_id"] == f"follow-up-{row.id}:generate:1"
    item = db.get(RoundItem, row.item_id)
    assert (item.role, item.parent_item_id) == ("followup", db.get(Attempt, main).round_item_id)
    version = db.get(QuestionVersion, item.question_version_id)
    assert (version.status, version.text, version.style) == ("followup", FOLLOW_UP_TEXT, "short")
    rubric = db.scalar(select(RubricVersion).where(RubricVersion.question_version_id == version.id))
    assert rubric is not None and rubric.id == item.rubric_version_id
    pending = db.scalar(select(Attempt).where(Attempt.round_item_id == item.id))
    assert (pending.attempt_kind, pending.parent_attempt_id, pending.outcome) == (
        "followup",
        main,
        "pending",
    )
    assert fake.follow_up_builds == 1 and len(fake.fake_follow_up.calls) == 1
    call = fake.fake_follow_up.calls[0]
    assert (call["task"], call["max_output_tokens"]) == (FOLLOW_UP_TASK, 4000)


def test_follow_up_answer_runs_the_pipeline_then_main_feedback(client, db, world):
    use(client)
    rid, main = answered(client, world)
    follow = answer_current(client, rid)
    post_answer(client, follow)
    state = state_of(client, rid)
    assert (state["part"], state["phase"], state["current_attempt"]["id"]) == (
        "main",
        "feedback",
        main,
    )
    body = client.get(f"/api/attempts/{follow}/result").json()
    assert (body["status"], body["assessment"]["status"]) == ("ready", "assessed")
    db.expire_all()
    assert db.query(FollowUp).count() == 1  # never a follow-up of a follow-up


@pytest.mark.parametrize("case", ["dont_know", "skip", "silence", "transcriber_fails"])
@pytest.mark.parametrize("timing", ["practice", "exam"])
def test_no_follow_up_without_usable_answer(client, db, world, case, timing):
    error = ProviderError("server", "openai", "Fake provider failure.", retryable=True)
    fake = use(
        client, transcriber=FakeTranscriber(error=error) if case == "transcriber_fails" else None
    )
    rid = start_round(client, world, timing, 2, follow_ups=ON)["id"]
    if case in ("dont_know", "skip"):
        act(client, rid, "question_ready", "question")
        act(client, rid, case, "preparation")
    else:
        main = answer_current(client, rid)
        post_answer(client, main, "silence.wav" if case == "silence" else "speech_mid.wav")
    state = state_of(client, rid)
    if timing == "practice":
        assert (state["part"], state["phase"]) == ("main", "feedback")
        if case == "transcriber_fails":
            assert state["current_attempt"]["outcome"] == "technical_failure"
    else:
        assert (state["part"], state["phase"], state["current_index"]) == ("main", "question", 1)
    db.expire_all()
    assert db.query(FollowUp).count() == 0
    assert fake.follow_up_builds == 0 and fake.fake_follow_up.calls == []


def test_follow_ups_off_never_calls_generator(client, world):
    fake = use(client)
    rid, _ = answered(client, world, follow_ups={"enabled": False})
    assert state_of(client, rid)["phase"] == "feedback"
    assert fake.follow_up_builds == 0


def test_generation_failure_keeps_round_moving(client, db, world):
    error = ProviderError("server", "anthropic", "Fake provider failure.", retryable=True)
    use(client, follow_up=FakeTextGenerator({}, error=error))
    rid, main = answered(client, world)
    state = state_of(client, rid)
    assert (state["part"], state["phase"]) == ("main", "processing")
    assert state["follow_up"]["status"] == "failed"
    assert state["follow_up"]["failure"] == {"kind": "server", "message": "Fake provider failure."}
    assert state["follow_up"]["retryable"] is True
    state = act(client, rid, "no_follow_up", "processing")
    assert (state["phase"], state["current_attempt"]["id"]) == ("feedback", main)
    report = client.get(f"/api/reports/rounds/{rid}").json()
    assert report["knowledge"]["assessed"] == 1
    assert [row["attempt_id"] for row in report["items"] if row["attempt_id"]] == [main]
    db.expire_all()
    assert db.query(RoundItem).filter(RoundItem.role == "followup").count() == 0


def test_rerun_does_not_generate_twice(client, db, world, settings):
    fake = use(client)
    rid, main = answered(client, world)
    pipeline.run(main, settings, fake)
    db.expire_all()
    assert db.query(FollowUp).count() == 1
    assert len(fake.fake_follow_up.calls) == 1
    assert state_of(client, rid)["part"] == "followup"


def test_late_result_after_continue_is_discarded(client, db, world):
    box = {}

    def continue_first(messages):
        act(client, box["rid"], "no_follow_up", "processing")
        return follow_up_for(passage_ids_in(messages))

    use(client, follow_up=FakeTextGenerator({FOLLOW_UP_TASK: continue_first}))
    box["rid"] = start_round(client, world, "practice", 2, follow_ups=ON)["id"]
    main = answer_current(client, box["rid"])
    post_answer(client, main)
    state = state_of(client, box["rid"])
    assert (state["part"], state["phase"]) == ("main", "feedback")
    db.expire_all()
    row = db.scalar(select(FollowUp))
    assert (row.status, row.item_id) == ("omitted", None)
    assert db.query(RoundItem).filter(RoundItem.role == "followup").count() == 0
    assert db.query(QuestionVersion).filter(QuestionVersion.status == "followup").count() == 0


def test_generation_while_paused_moves_on_resume(client, db, world):
    box = {}

    def pause_first(messages):
        act(client, box["rid"], "pause", "processing")
        return follow_up_for(passage_ids_in(messages))

    use(client, follow_up=FakeTextGenerator({FOLLOW_UP_TASK: pause_first}))
    box["rid"] = start_round(client, world, "practice", 2, follow_ups=ON)["id"]
    main = answer_current(client, box["rid"])
    post_answer(client, main)
    state = state_of(client, box["rid"])
    assert (state["status"], state["phase"], state["part"]) == ("paused", "processing", "main")
    assert state["follow_up"]["status"] == "ready"
    state = act(client, box["rid"], "resume", "processing")
    assert (state["status"], state["phase"], state["part"]) == ("active", "question", "followup")
    assert state["current"]["text"] == FOLLOW_UP_TEXT


def test_read_during_generation_shows_generating(client, world):
    box = {}

    def look(messages):
        box["seen"] = state_of(client, box["rid"])
        return follow_up_for(passage_ids_in(messages))

    use(client, follow_up=FakeTextGenerator({FOLLOW_UP_TASK: look}))
    box["rid"] = start_round(client, world, "practice", 2, follow_ups=ON)["id"]
    main = answer_current(client, box["rid"])
    post_answer(client, main)
    assert box["seen"]["phase"] == "processing"
    assert box["seen"]["follow_up"]["status"] == "generating"
    assert box["seen"]["follow_up"]["failure"] is None
