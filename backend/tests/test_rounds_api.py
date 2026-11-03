import json

import pytest
from fastapi import HTTPException
from test_rounds_support import Clock, answer_keys_in, make_recording, make_world
from test_topics_support import make_subject, make_topic, upgrade

from app.config import Settings
from app.db.session import SessionLocal
from app.features.rounds import service
from app.features.rounds.models import Attempt, Round, RoundEvent, RoundItem


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


def create(client, world, **config):
    config.setdefault("scope", {"kind": "subject"})
    return client.post("/api/rounds", json={"subject_id": world["subject"].id, "config": config})


def start(client, world, **config) -> dict:
    response = create(client, world, **config)
    assert response.status_code == 201, response.text
    return response.json()


def act(client, round_id: int, action: str, from_phase: str, **extra):
    return client.post(
        f"/api/rounds/{round_id}/actions",
        json={"action": action, "from_phase": from_phase, **extra},
    )


def ok(client, round_id: int, action: str, from_phase: str, **extra) -> dict:
    response = act(client, round_id, action, from_phase, **extra)
    assert response.status_code == 200, response.text
    return response.json()


def answer(client, round_id: int) -> dict:
    """question → preparation → recording → stop."""
    ok(client, round_id, "question_ready", "question")
    ok(client, round_id, "start_answer", "preparation", start_reason="button")
    return ok(client, round_id, "stop", "recording")


def test_create_round_snapshot_and_first_attempt(client, world, db, settings):
    state = start(client, world, question_count=3, band_bounds=[10, 20, 30])
    assert state["status"] == "active"
    assert state["phase"] == "question"
    assert (state["current_index"], state["total"]) == (0, 3)
    assert state["config"]["band_bounds"] == list(settings.factual_band_bounds)
    assert state["current_attempt"]["outcome"] == "pending"
    assert state["current_attempt"]["attempt_kind"] == "initial"
    assert state["current"]["item_id"] == state["items"][0]["id"]
    assert [i["status"] for i in state["items"]] == ["current", "pending", "pending"]
    row = db.get(Round, state["id"])
    assert json.loads(row.config_json)["band_bounds"] == [30, 60, 80]
    assert db.query(Attempt).count() == 1
    # Round robin over the two topics.
    topics = {q.id: q.family_id for q in world["questions"]}
    assert len({topics[i["question_version_id"]] for i in state["items"]}) == 3


def test_upcoming_names_the_next_question(client, world, db):
    from app.features.questions import service as questions

    state = start(client, world, question_count=2)
    second = state["items"][1]["question_version_id"]
    text = questions.get_question(db, second, with_rubric=False).text
    assert state["upcoming"] == [text]
    skipped = ok(client, state["id"], "skip", "question")
    assert skipped["upcoming"] == [text]  # feedback: the next question is still ahead
    state = ok(client, state["id"], "next", skipped["phase"])
    assert state["current_index"] == 1
    assert state["upcoming"] == []


def test_create_insufficient_409_with_available(client, world, db):
    athens = world["topics"][0]
    response = create(
        client, world, question_count=5, scope={"kind": "topics", "topic_ids": [athens.id]}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "message": "Not enough questions for the selected settings: 4 available.",
        "available": 4,
    }
    style = create(client, world, question_count=1, style="short")
    assert style.json()["detail"]["available"] == 0
    db.expire_all()
    for model in (Round, RoundItem, Attempt, RoundEvent):
        assert db.query(model).count() == 0


def test_topic_and_document_scope(client, world):
    athens = world["topics"][0]
    state = start(
        client, world, question_count=4, scope={"kind": "topics", "topic_ids": [athens.id]}
    )
    athens_ids = {q.id for q in world["questions"][:4]}
    assert {i["question_version_id"] for i in state["items"]} == athens_ids
    docs = start(
        client,
        world,
        question_count=8,
        scope={"kind": "documents", "document_ids": [world["document_id"]]},
    )
    assert docs["total"] == 8


def test_foreign_topic_422(client, world, db):
    other = make_subject(db, "Biologia")
    topic = make_topic(db, other.id, "Komórka")
    response = create(client, world, scope={"kind": "topics", "topic_ids": [topic.id]})
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "The selected topics or documents do not belong to this subject."
    )
    response = create(client, world, scope={"kind": "documents", "document_ids": [999]})
    assert response.status_code == 422


def test_unknown_subject_404(client, db):
    response = client.post(
        "/api/rounds", json={"subject_id": 999, "config": {"scope": {"kind": "subject"}}}
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Subject not found."
    assert client.get("/api/rounds/999").status_code == 404
    assert client.get("/api/rounds/999").json()["detail"] == "Round not found."


def test_premium_audio_filled_from_settings(client, world, settings):
    state = start(client, world, question_count=1, question_audio={"mode": "premium"})
    audio = state["config"]["question_audio"]
    assert audio == {
        "mode": "premium",
        "provider": settings.voice_provider,
        "voice": settings.voice_name,
        "rate": settings.voice_rate,
    }
    client.app.state.settings = settings.model_copy(update={"voice_provider": "browser"})
    response = create(client, world, question_count=1, question_audio={"mode": "premium"})
    assert response.status_code == 422
    assert response.json()["detail"] == "Choose a premium voice in Settings."


def test_no_answer_leaks_anywhere(client, world, db):
    responses = []
    for timing in ("practice", "exam"):
        state = start(client, world, question_count=3, feedback_timing=timing)
        rid = state["id"]
        responses.append(state)
        for _ in range(3):
            responses.append(answer(client, rid))
            if timing == "practice":
                current = responses[-1]["current_attempt"]["id"]
                service.mark_attempt_result(db, current, "ready")
                responses.append(client.get(f"/api/rounds/{rid}").json())
                responses.append(ok(client, rid, "next", "feedback"))
        responses.append(client.get(f"/api/rounds/{rid}").json())
        responses.append(client.get(f"/api/rounds/{rid}/summary").json())
    responses.append(client.get("/api/rounds", params={"subject_id": world["subject"].id}).json())
    for body in responses:
        assert answer_keys_in(body) == set()
    assert "Demokracja ateńska" not in json.dumps(responses, ensure_ascii=False)


def test_start_answer_without_reason_422(client, world):
    rid = start(client, world, question_count=1)["id"]
    ok(client, rid, "question_ready", "question")
    assert act(client, rid, "start_answer", "preparation").status_code == 422


def test_practice_full_flow(client, world, db):
    rid = start(client, world, question_count=3)["id"]
    state = answer(client, rid)
    assert state["phase"] == "processing"
    first = state["current_attempt"]
    assert (first["outcome"], first["processing_status"]) == ("answered", "processing")
    assert first["start_reason"] == "button"
    state = service.mark_attempt_result(db, first["id"], "ready").model_dump()
    assert state["phase"] == "feedback"
    state = ok(client, rid, "next", "feedback")
    assert (state["phase"], state["current_index"]) == ("question", 1)
    assert state["current_attempt"]["outcome"] == "pending"

    ok(client, rid, "question_ready", "question")
    state = ok(client, rid, "dont_know", "preparation")
    assert (state["phase"], state["current_attempt"]["outcome"]) == ("feedback", "dont_know")
    ok(client, rid, "next", "feedback")

    state = ok(client, rid, "skip", "question")
    assert (state["phase"], state["current_attempt"]["outcome"]) == ("feedback", "skipped")
    state = ok(client, rid, "next", "feedback")
    assert (state["status"], state["phase"], state["current"]) == ("finished", "summary", None)
    assert [i["status"] for i in state["items"]] == ["done", "done", "done"]

    summary = client.get(f"/api/rounds/{rid}/summary").json()
    assert summary["status"] == "finished"
    assert summary["provisional"] is False
    assert summary["outcomes"] == {"answered": 1, "dont_know": 1, "skipped": 1}
    assert db.get(Round, rid).finished_at is not None
    assert act(client, rid, "pause", "summary").json()["detail"] == "The round is over."


def test_exam_flow_moves_on_while_processing(client, world, db):
    rid = start(client, world, question_count=2, feedback_timing="exam")["id"]
    state = answer(client, rid)
    first = state["items"][0]["latest_attempt"]
    assert (state["phase"], state["current_index"]) == ("question", 1)
    assert first["processing_status"] == "processing"
    state = answer(client, rid)
    assert (state["status"], state["phase"]) == ("finished", "summary")
    second = state["items"][1]["latest_attempt"]

    summary = client.get(f"/api/rounds/{rid}/summary").json()
    assert summary["provisional"] is True
    assert summary["unresolved_attempt_ids"] == [first["id"], second["id"]]
    state = service.mark_attempt_result(db, first["id"], "ready")
    assert state.phase == "summary"
    assert client.get(f"/api/rounds/{rid}/summary").json()["provisional"] is True
    service.mark_attempt_result(db, second["id"], "ready")
    summary = client.get(f"/api/rounds/{rid}/summary").json()
    assert (summary["provisional"], summary["outcomes"]) == (False, {"answered": 2})


def test_exam_dont_know_and_skip_move_on(client, world):
    rid = start(client, world, question_count=3, feedback_timing="exam")["id"]
    assert ok(client, rid, "skip", "question")["current_index"] == 1
    ok(client, rid, "question_ready", "question")
    state = ok(client, rid, "mic_failure", "preparation")
    assert (state["current_index"], state["phase"]) == (2, "question")
    assert act(client, rid, "next", "question").status_code == 409


def test_mid_question_pause_interrupts_and_resume_relinks(client, world, db):
    rid = start(client, world, question_count=2)["id"]
    ok(client, rid, "question_ready", "question")
    state = ok(client, rid, "start_answer", "preparation", start_reason="auto")
    first = state["current_attempt"]["id"]
    recording = make_recording(db)
    service.attach_recording(db, first, recording.id)

    state = ok(client, rid, "pause", "recording")
    assert (state["status"], state["phase"]) == ("paused", "recording")
    assert state["current_attempt"]["outcome"] == "interrupted"
    assert act(client, rid, "stop", "recording").json()["detail"] == "The round is paused."

    state = ok(client, rid, "resume", "recording")
    assert (state["status"], state["phase"]) == ("active", "question")
    second = state["current_attempt"]
    assert second["id"] != first
    assert (second["parent_attempt_id"], second["outcome"]) == (first, "pending")
    assert second["attempt_kind"] == "initial"
    assert second["recording_id"] is None
    db.expire_all()
    interrupted = db.get(Attempt, first)
    assert (interrupted.recording_id, interrupted.outcome) == (recording.id, "interrupted")
    assert interrupted.ended_at is not None
    assert service.get_attempt_context(db, first).interrupted is True

    state = answer(client, rid)
    service.mark_attempt_result(db, state["current_attempt"]["id"], "ready")
    summary = client.get(f"/api/rounds/{rid}/summary").json()
    assert summary["interrupted_attempt_ids"] == [first]
    # The interrupted attempt never counts as the item's outcome.
    assert summary["outcomes"] == {"answered": 1, "pending": 1}
    assert summary["unresolved_attempt_ids"] == []


def test_between_questions_pause_keeps_attempt(client, world, db):
    rid = start(client, world, question_count=2)["id"]
    before = client.get(f"/api/rounds/{rid}").json()["current_attempt"]
    state = ok(client, rid, "pause", "question")
    assert state["status"] == "paused"
    state = ok(client, rid, "resume", "question")
    assert (state["phase"], state["current_attempt"]) == ("question", before)

    state = answer(client, rid)
    attempt = state["current_attempt"]["id"]
    service.mark_attempt_result(db, attempt, "ready")
    ok(client, rid, "pause", "feedback")
    state = ok(client, rid, "resume", "feedback")
    assert (state["phase"], state["current_attempt"]["outcome"]) == ("feedback", "answered")
    assert db.query(Attempt).count() == 1


def test_pause_during_processing_resumes_to_feedback(client, world, db):
    rid = start(client, world, question_count=1)["id"]
    attempt = answer(client, rid)["current_attempt"]["id"]
    ok(client, rid, "pause", "processing")
    state = service.mark_attempt_result(db, attempt, "ready")
    assert (state.status, state.phase) == ("paused", "processing")
    state = ok(client, rid, "resume", "processing")
    assert (state["phase"], state["current_attempt"]["outcome"]) == ("feedback", "answered")


def test_pause_count_and_duration(client, world, clock):
    rid = start(client, world, question_count=2)["id"]
    clock.advance(seconds=5)
    ok(client, rid, "pause", "question")
    clock.advance(seconds=10)
    assert client.get(f"/api/rounds/{rid}").json()["paused_ms"] == 10_000
    ok(client, rid, "resume", "question")
    clock.advance(seconds=30)
    ok(client, rid, "question_ready", "question")
    ok(client, rid, "pause", "preparation")
    clock.advance(milliseconds=2500)
    state = ok(client, rid, "resume", "preparation")
    assert (state["pause_count"], state["paused_ms"]) == (2, 12_500)
    clock.advance(seconds=60)
    summary = client.get(f"/api/rounds/{rid}/summary").json()
    assert (summary["pause_count"], summary["paused_ms"]) == (2, 12_500)


def test_duplicate_action_is_idempotent(client, world, db):
    rid = start(client, world, question_count=2)["id"]
    first = ok(client, rid, "question_ready", "question")
    events = db.query(RoundEvent).count()
    again = ok(client, rid, "question_ready", "question")
    for state in (first, again):  # the clock's server_now is the time of each read
        state["clock"].pop("server_now")
    assert again == first
    ok(client, rid, "pause", "preparation")
    paused = ok(client, rid, "pause", "preparation")
    assert paused["status"] == "paused"
    resumed = ok(client, rid, "resume", "preparation")
    assert ok(client, rid, "resume", "preparation") == resumed
    db.expire_all()
    assert db.query(RoundEvent).count() == events + 2
    assert db.query(Attempt).count() == 2  # one interrupted, one restarted


def test_stale_action_409(client, world):
    rid = start(client, world, question_count=2)["id"]
    ok(client, rid, "question_ready", "question")
    ok(client, rid, "start_answer", "preparation", start_reason="button")
    stale = act(client, rid, "skip", "question")
    assert stale.status_code == 409
    assert stale.json()["detail"] == "This action is not available right now."
    assert act(client, rid, "stop", "preparation").status_code == 409
    assert client.get(f"/api/rounds/{rid}").json()["phase"] == "recording"


def test_failed_result_is_technical_failure_and_provisional(client, world, db):
    rid = start(client, world, question_count=1)["id"]
    attempt = answer(client, rid)["current_attempt"]["id"]
    state = service.mark_attempt_result(db, attempt, "failed")
    assert state.phase == "feedback"
    assert (state.current_attempt.outcome, state.current_attempt.processing_status) == (
        "technical_failure",
        "failed",
    )
    assert service.mark_attempt_result(db, attempt, "failed") == state
    state = ok(client, rid, "next", "feedback")
    assert state["status"] == "finished"
    summary = client.get(f"/api/rounds/{rid}/summary").json()
    assert summary["provisional"] is True
    assert summary["unresolved_attempt_ids"] == [attempt]
    assert summary["outcomes"] == {"technical_failure": 1}
    assert "dont_know" not in summary["outcomes"] and "skipped" not in summary["outcomes"]


def test_snapshot_immutable_after_settings_change(client, world, db, settings):
    state = start(client, world, question_count=1, prep_seconds=0, answer_seconds=60)
    stored = db.get(Round, state["id"]).config_json
    client.app.state.settings = Settings(
        _env_file=None,
        data_dir=settings.data_dir,
        frontend_dist=settings.frontend_dist,
        env_file_path=settings.env_file_path,
        factual_band_bounds=(20, 50, 90),
        factual_weights=(50, 30),
        voice_name="other-voice",
    )
    again = client.get(f"/api/rounds/{state['id']}").json()
    assert again["config"] == state["config"]
    assert again["config"]["band_bounds"] == [30, 60, 80]
    assert again["config"]["factual_weights"] == [30, 50]  # snapshot at creation
    assert (again["config"]["prep_seconds"], again["config"]["answer_seconds"]) == (0, 60)
    answer(client, state["id"])
    db.expire_all()
    assert db.get(Round, state["id"]).config_json == stored
    newer = start(client, world, question_count=1)
    assert newer["config"]["band_bounds"] == [20, 50, 90]


def test_attempt_context_for_pipeline(client, world, db):
    state = start(client, world, question_count=2, feedback_timing="exam")
    attempt = state["current_attempt"]["id"]
    context = service.get_attempt_context(db, attempt)
    item = db.get(RoundItem, state["items"][0]["id"])
    assert context.round_id == state["id"]
    assert context.item_id == item.id
    assert context.subject_id == world["subject"].id
    assert context.question_version_id == item.question_version_id
    assert context.rubric_version_id == item.rubric_version_id
    assert (context.attempt_kind, context.interrupted) == ("initial", False)
    assert context.config.feedback_timing == "exam"
    assert context.config.band_bounds == (30, 60, 80)


def test_last_asked_questions_come_later(client, world):
    first = start(client, world, question_count=4)
    second = start(client, world, question_count=4)
    first_ids = {i["question_version_id"] for i in first["items"]}
    second_ids = {i["question_version_id"] for i in second["items"]}
    assert first_ids.isdisjoint(second_ids)


def test_list_rounds_newest_first(client, world, clock):
    older = start(client, world, question_count=1)["id"]
    clock.advance(minutes=5)
    newer = start(client, world, question_count=2, feedback_timing="exam")["id"]
    ok(client, newer, "skip", "question")
    listed = client.get("/api/rounds", params={"subject_id": world["subject"].id}).json()
    assert [r["id"] for r in listed] == [newer, older]
    assert (listed[0]["total"], listed[0]["done"], listed[0]["feedback_timing"]) == (2, 1, "exam")
    assert listed[1]["finished_at"] is None


def test_abandon(client, world, db):
    rid = start(client, world, question_count=3)["id"]
    ok(client, rid, "question_ready", "question")
    state = ok(client, rid, "abandon", "preparation")
    assert (state["status"], state["phase"], state["current"]) == ("abandoned", "summary", None)
    assert ok(client, rid, "abandon", "preparation") == state  # a repeated click
    assert act(client, rid, "resume", "summary").json()["detail"] == "The round is over."
    summary = client.get(f"/api/rounds/{rid}/summary").json()
    assert (summary["status"], summary["provisional"]) == ("abandoned", True)
    assert db.get(Round, rid).finished_at is not None


# ---- skip of a retried exam answer ----


def test_exam_retried_skip_with_attempt_id_skips_once(client, world):
    rid = start(client, world, question_count=3, feedback_timing="exam")["id"]
    first = client.get(f"/api/rounds/{rid}").json()["current_attempt"]["id"]
    moved = ok(client, rid, "skip", "question", attempt_id=first)
    assert moved["current_index"] == 1
    again = ok(client, rid, "skip", "question", attempt_id=first)
    assert again == moved  # the retry is recognised, the second question is not skipped
    stale = act(client, rid, "question_ready", "question", attempt_id=first)
    assert stale.status_code == 409


def test_parallel_action_loses_the_version_race(client, world, db, settings):
    rid = start(client, world, question_count=2)["id"]
    with SessionLocal(settings.effective_database_url) as other:
        other.get(Round, rid)  # loaded before the first request commits
        ok(client, rid, "question_ready", "question")
        state = service.apply_action(
            other, rid, service.ActionIn(action="question_ready", from_phase="question")
        )
        assert state.phase == "preparation"
    db.expire_all()
    assert db.query(RoundEvent).count() == 1
    assert db.query(Attempt).count() == 1


def test_attach_recording_guards(client, world, db):
    rid = start(client, world, question_count=2)["id"]
    attempt = answer(client, rid)["current_attempt"]["id"]
    one, two = make_recording(db), make_recording(db)
    service.attach_recording(db, attempt, one.id)
    service.attach_recording(db, attempt, one.id)  # retry: no change
    with pytest.raises(HTTPException) as error:
        service.attach_recording(db, attempt, two.id)
    assert error.value.status_code == 409
    service.mark_attempt_result(db, attempt, "ready")
    nxt = ok(client, rid, "next", "feedback")["current_attempt"]["id"]
    with pytest.raises(HTTPException):
        service.attach_recording(db, nxt, one.id)  # already used by another attempt


def test_mark_attempt_result_guards(client, world, db):
    rid = start(client, world, question_count=2)["id"]
    pending = client.get(f"/api/rounds/{rid}").json()["current_attempt"]["id"]
    with pytest.raises(HTTPException) as error:
        service.mark_attempt_result(db, pending, "ready")  # nothing was answered yet
    assert error.value.status_code == 409
    attempt = answer(client, rid)["current_attempt"]["id"]
    db.expire_all()  # the API wrote through another session
    state = service.mark_attempt_result(db, attempt, "failed")
    assert state.current_attempt.outcome == "technical_failure"
    state = service.mark_attempt_result(db, attempt, "ready")  # a retried pipeline succeeded
    assert (state.current_attempt.outcome, state.current_attempt.processing_status) == (
        "answered",
        "ready",
    )
    with pytest.raises(HTTPException):
        service.mark_attempt_result(db, attempt, "failed")  # ready is final


def test_premium_audio_other_provider_gets_no_settings_voice(client, world, settings):
    state = start(
        client,
        world,
        question_count=1,
        question_audio={"mode": "premium", "provider": "azure_speech"},
    )
    assert state["config"]["question_audio"] == {
        "mode": "premium",
        "provider": "azure_speech",
        "voice": "pl-PL-ZofiaNeural",
        "rate": settings.voice_rate,
    }
    state = start(client, world, question_count=1, question_audio={"mode": "off", "voice": "x"})
    assert state["config"]["question_audio"] == {
        "mode": "off",
        "provider": None,
        "voice": None,
        "rate": None,
    }
