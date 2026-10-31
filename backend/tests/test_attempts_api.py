import pytest
from test_attempts_support import (
    FakeProviders,
    act,
    answer_current,
    answered_attempt,
    override_providers,
    post_audio,
    post_events,
    result,
    start_round,
    wav,
)
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.config import Settings
from app.db.session import SessionLocal
from app.features.attempts import pipeline
from app.features.attempts.models import AttemptProcessing
from app.features.recording.models import Recording
from app.features.rounds import service as rounds
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
def fake(client):
    fake = FakeProviders()
    override_providers(client.app, fake)
    return fake


@pytest.fixture
def scheduled(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(pipeline, "schedule", lambda bg, attempt_id, s, p: calls.append(attempt_id))
    return calls


def recordings_on_disk(settings) -> list:
    folder = settings.data_dir / "recordings"
    return sorted(folder.glob("*.wav")) if folder.is_dir() else []


def test_events_stored_and_idempotent(client, world, db, scheduled):
    _, attempt = answered_attempt(client, world)
    first = post_events(client, attempt)
    assert first.status_code == 200
    assert first.json() == {"attempt_id": attempt, "stored": True}
    again = post_events(client, attempt)
    assert (again.status_code, again.json()) == (200, first.json())
    db.expire_all()
    row = db.query(AttemptProcessing).one()
    assert (row.attempt_id, row.status, row.stage) == (attempt, "processing", "waiting_for_audio")
    assert '"listening_available":true' in row.events_json
    assert scheduled == []


def test_different_events_409(client, world, db, scheduled):
    _, attempt = answered_attempt(client, world)
    post_events(client, attempt)
    response = post_events(client, attempt, stopped_at=30000.0)
    assert response.status_code == 409
    assert response.json()["detail"] == "Different events were already saved for this attempt."
    db.expire_all()
    assert '"stopped_at":21400.0' in db.query(AttemptProcessing).one().events_json


def test_events_validation_422(client, world, scheduled):
    _, attempt = answered_attempt(client, world)
    assert post_events(client, attempt, start_reason="voice").status_code == 422


def test_events_for_pending_attempt_409(client, world, db, scheduled):
    rid = start_round(client, world)["id"]
    act(client, rid, "question_ready", "question")
    state = act(client, rid, "start_answer", "preparation", start_reason="auto")
    attempt = state["current_attempt"]["id"]  # still recording: the client sends stop first
    response = post_events(client, attempt)
    assert response.status_code == 409
    assert response.json()["detail"] == "This attempt has no recorded answer."
    audio = post_audio(client, attempt)
    assert (audio.status_code, audio.json()["detail"]) == (
        409,
        "This attempt has no recorded answer.",
    )
    assert db.query(Recording).count() == 0
    assert recordings_on_disk(client.app.state.settings) == []


def test_unknown_attempt_404(client, db, scheduled):
    responses = [
        post_events(client, 999),
        post_audio(client, 999),
        client.get("/api/attempts/999/result"),
        client.post("/api/attempts/999/retry"),
    ]
    for response in responses:
        assert response.status_code == 404
        assert response.json()["detail"] == "Attempt not found."


def test_audio_stored_and_linked_201(client, world, db, settings, scheduled):
    _, attempt = answered_attempt(client, world)
    response = post_audio(client, attempt)
    assert response.status_code == 201
    body = response.json()
    assert body["attempt_id"] == attempt
    assert body["duration_ms"] == 7510
    db.expire_all()
    recording = db.get(Recording, body["recording_id"])
    assert recording.purpose == "answer"
    assert (settings.data_dir / recording.stored_path).read_bytes() == wav("speech_mid.wav")
    assert recording.stored_path.startswith("recordings/")
    assert db.get(Attempt, attempt).recording_id == recording.id
    assert db.query(AttemptProcessing).one().stage == "saved"


def test_audio_again_returns_existing_200_and_discards_new(client, world, db, settings, scheduled):
    _, attempt = answered_attempt(client, world)
    first = post_audio(client, attempt).json()
    again = post_audio(client, attempt, "silence.wav")
    assert again.status_code == 200
    assert again.json() == first
    db.expire_all()
    assert db.query(Recording).count() == 1
    assert len(recordings_on_disk(settings)) == 1


def test_racing_uploads_leave_one_recording(client, world, db, settings, monkeypatch):
    """The second upload is stored while the first is between its check and its link."""
    from app.features.attempts import service
    from app.features.recording import service as recordings

    _, attempt = answered_attempt(client, world)
    original = recordings.create_recording
    raced: list = []

    def create_and_race(session, s, purpose, data):
        out = original(session, s, purpose, data)
        if not raced:
            raced.append(True)
            with SessionLocal(settings.effective_database_url) as other:
                raced.append(service.save_audio(other, settings, attempt, wav("silence.wav")))
        return out

    monkeypatch.setattr(recordings, "create_recording", create_and_race)
    with SessionLocal(settings.effective_database_url) as session:
        out, created = service.save_audio(session, settings, attempt, wav("speech_mid.wav"))
    winner, winner_created = raced[1]
    assert (winner_created, created) == (True, False)
    assert out == winner
    db.expire_all()
    assert db.query(Recording).count() == 1
    assert len(recordings_on_disk(settings)) == 1
    assert db.get(Attempt, attempt).recording_id == winner.recording_id


def test_audio_without_any_key_still_stored(tmp_path, db, settings):
    from conftest import local_client

    from app.main import create_app

    keyless = Settings(
        _env_file=None,
        base_env_file=None,
        data_dir=settings.data_dir,
        frontend_dist=tmp_path / "dist",
        env_file_path=tmp_path / ".env",
        openai_api_key=None,
        anthropic_api_key=None,
    )
    client = local_client(create_app(keyless))  # real RegistryProviders, no override
    world = make_world(db)
    _, attempt = answered_attempt(client, world)
    response = post_audio(client, attempt)
    assert response.status_code == 201
    db.expire_all()
    recording = db.get(Recording, response.json()["recording_id"])
    assert (settings.data_dir / recording.stored_path).is_file()
    assert db.get(Attempt, attempt).recording_id == recording.id


def test_audio_bad_format_415_and_nothing_linked(client, world, db, settings, scheduled):
    _, attempt = answered_attempt(client, world)
    response = post_audio(client, attempt, data=b"RIFF\x00\x00\x00\x00WAVEjunk")
    assert response.status_code == 415
    assert response.json()["detail"] == "The recording must be in WAV format, 16 kHz, mono."
    assert post_audio(client, attempt, data=b"").status_code == 422
    db.expire_all()
    assert db.get(Attempt, attempt).recording_id is None
    assert db.query(Recording).count() == 0
    assert recordings_on_disk(settings) == []


def test_interrupted_attempt_keeps_audio_never_scheduled(client, world, db, scheduled):
    rid = start_round(client, world)["id"]
    act(client, rid, "question_ready", "question")
    state = act(client, rid, "start_answer", "preparation", start_reason="auto")
    attempt = state["current_attempt"]["id"]
    act(client, rid, "pause", "recording")
    assert post_events(client, attempt, interrupted=True).status_code == 200
    response = post_audio(client, attempt)
    assert response.status_code == 201
    body = result(client, attempt)
    assert (body["outcome"], body["status"], body["stage"]) == (
        "interrupted",
        "ready",
        "not_assessed",
    )
    assert body["recording"] == {"id": response.json()["recording_id"], "duration_ms": 7510}
    assert body["assessment"] is None and body["transcript"] is None
    assert scheduled == []


@pytest.mark.parametrize("audio_first", [False, True])
def test_schedule_only_when_events_and_audio_both_present(client, world, scheduled, audio_first):
    _, attempt = answered_attempt(client, world)
    if audio_first:
        post_audio(client, attempt)
        assert scheduled == []
        post_events(client, attempt)
    else:
        post_events(client, attempt)
        assert scheduled == []
        post_audio(client, attempt)
    assert scheduled == [attempt]


def test_result_states_without_pipeline(client, world, fake, scheduled):
    rid = start_round(client, world, count=4)["id"]
    pending = client.get(f"/api/rounds/{rid}").json()["current_attempt"]["id"]
    response = client.get(f"/api/attempts/{pending}/result")
    assert (response.status_code, response.json()["detail"]) == (
        409,
        "This attempt has no answer yet.",
    )

    answered = answer_current(client, rid)
    body = result(client, answered)
    assert (body["outcome"], body["status"], body["stage"]) == (
        "answered",
        "processing",
        "waiting_for_audio",
    )
    assert body["recording"] is None and body["failure"] is None
    # No pipeline here: move the round on by marking the answer ready by hand.
    with SessionLocal(client.app.state.settings.effective_database_url) as s:
        rounds.mark_attempt_result(s, answered, "ready")
    act(client, rid, "next", "feedback")

    for action in ("dont_know", "skip"):
        if action == "dont_know":
            act(client, rid, "question_ready", "question")
            attempt = act(client, rid, "dont_know", "preparation")["current_attempt"]["id"]
        else:
            attempt = act(client, rid, "skip", "question")["current_attempt"]["id"]
        body = result(client, attempt)
        assert (body["status"], body["stage"]) == ("ready", "done")
        assessment = body["assessment"]
        assert (assessment["status"], assessment["band"]) == ("no_answer", "weak")
        assert assessment["attempts_used"] == 0
        act(client, rid, "next", "feedback")

    act(client, rid, "question_ready", "question")
    attempt = act(client, rid, "mic_failure", "preparation")["current_attempt"]["id"]
    body = result(client, attempt)
    assert (body["outcome"], body["status"]) == ("technical_failure", "failed")
    assert body["failure"] == {
        "kind": "microphone",
        "message": "The microphone stopped during the answer, so nothing was recorded.",
        "retryable": False,
    }
    assert body["assessment"] is None
    assert fake.fake_assessor.calls == [] and fake.assessor_builds == 0


def test_exam_round_result_withheld_until_round_ends(client, world, fake):
    rid = start_round(client, world, "exam", count=2)["id"]
    act(client, rid, "question_ready", "question")
    attempt = act(client, rid, "dont_know", "preparation")["items"][0]["latest_attempt"]["id"]
    body = result(client, attempt)
    assert (body["status"], body["withheld"], body["assessment"]) == ("ready", True, None)
    assert body["transcript"] is None and body["metrics"] is None
    act(client, rid, "pause", "question")
    body = result(client, attempt)
    assert (body["withheld"], body["assessment"]) == (True, None)
    act(client, rid, "abandon", "question")
    body = result(client, attempt)
    assert body["withheld"] is False
    assert (body["assessment"]["status"], body["assessment"]["band"]) == ("no_answer", "weak")


def test_retry_nothing_to_retry_409(client, world, scheduled):
    _, attempt = answered_attempt(client, world)
    response = client.post(f"/api/attempts/{attempt}/retry")
    assert (response.status_code, response.json()["detail"]) == (
        409,
        "This attempt cannot be retried.",
    )
