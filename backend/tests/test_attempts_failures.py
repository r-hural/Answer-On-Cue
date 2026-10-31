import json

import pytest
from test_assessment_support import point
from test_attempts_support import (
    ASSESS_TASK,
    FakeProviders,
    act,
    ai_for,
    answered_attempt,
    override_providers,
    post_answer,
    post_events,
    result,
    start_round,
    wav,
)
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.attempts import pipeline
from app.features.attempts.models import AttemptProcessing
from app.features.providers.fakes import FakeTextGenerator, FakeTranscriber
from app.features.providers.types import ProviderError
from app.features.recording.models import Recording


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    return make_world(db)


CANNOT_RETRY = "This attempt cannot be retried."


def provider_error(kind: str) -> ProviderError:
    return ProviderError(kind, "openai", f"Provider failure: {kind}.", retryable=True)


def use(client, **fakes) -> FakeProviders:
    fake = FakeProviders(**fakes)
    override_providers(client.app, fake)
    return fake


def band_values(value) -> list:
    """Every non-null "band" anywhere in a JSON value."""
    found = []
    if isinstance(value, dict):
        found += [v for k, v in value.items() if k == "band" and v is not None]
        for v in value.values():
            found += band_values(v)
    elif isinstance(value, list):
        for v in value:
            found += band_values(v)
    return found


def assert_unresolved(client, db, settings, rid: int, attempt: int) -> dict:
    """A technical failure: no score anywhere, the round stays provisional, the audio stays."""
    body = result(client, attempt)
    assert body["status"] == "failed"
    assert body["assessment"] is None and body["transcript"] is None and body["metrics"] is None
    assert body["failure"]["retryable"] is True
    assert band_values(body) == []
    state = client.get(f"/api/rounds/{rid}").json()
    latest = next(
        i["latest_attempt"] for i in state["items"] if i["latest_attempt"]["id"] == attempt
    )
    assert latest["outcome"] == "technical_failure"
    assert latest["processing_status"] == "failed"
    summary = client.get(f"/api/rounds/{rid}/summary").json()
    assert summary["provisional"] is True
    assert attempt in summary["unresolved_attempt_ids"]
    assert band_values(summary) == []
    db.expire_all()
    recording = db.get(Recording, body["recording"]["id"])
    assert (settings.data_dir / recording.stored_path).read_bytes() == wav("speech_mid.wav")
    return body


@pytest.mark.parametrize("kind", ["timeout", "rate_limit", "server", "network"])
def test_transcription_provider_error_is_unresolved_not_weak(client, world, db, settings, kind):
    fake = use(client, transcriber=FakeTranscriber(error=provider_error(kind)))
    rid, attempt = answered_attempt(client, world)
    post_answer(client, attempt)
    body = assert_unresolved(client, db, settings, rid, attempt)
    assert body["failure"]["kind"] == kind
    assert body["failure"]["message"] == f"Provider failure: {kind}."
    assert body["stage"] == "transcribing"
    assert fake.fake_assessor.calls == [] and fake.assessor_builds == 0


def test_missing_key_is_failed_unavailable_and_audio_kept(client, world, db, settings):
    # Real RegistryProviders; keys cleared even if the environment has some.
    keyless = {"openai_api_key": None, "anthropic_api_key": None}
    client.app.state.settings = settings.model_copy(update=keyless)
    rid, attempt = answered_attempt(client, world)
    sent, uploaded = post_answer(client, attempt)
    assert (sent.status_code, uploaded.status_code) == (200, 201)
    body = assert_unresolved(client, db, settings, rid, attempt)
    assert body["failure"]["kind"] == "unavailable"
    assert body["failure"]["message"] == "No API key for: openai. Add it in Settings."


def test_assessment_provider_error_keeps_transcript(client, world, db, settings):
    fake = use(client, assessor=FakeTextGenerator({}, error=provider_error("timeout")))
    rid, attempt = answered_attempt(client, world)
    post_answer(client, attempt)
    body = assert_unresolved(client, db, settings, rid, attempt)
    assert (body["failure"]["kind"], body["stage"]) == ("timeout", "assessing")
    row = db.query(AttemptProcessing).one()
    assert row.transcript_text == "To jest testowa odpowiedź."
    assert row.metrics_json is not None and row.assessment_json is None
    assert len(fake.fake_transcriber.calls) == 1


def test_invalid_output_twice_is_failed_invalid_output(client, world, db, settings):
    incomplete = ai_for(points=[point("p1")])  # p2 has no judgement
    fake = use(client, assessor=FakeTextGenerator({ASSESS_TASK: incomplete}))
    rid, attempt = answered_attempt(client, world)
    post_answer(client, attempt)
    body = assert_unresolved(client, db, settings, rid, attempt)
    assert body["failure"] == {
        "kind": "invalid_output",
        "message": "The assessment failed: the model returned an invalid result.",
        "retryable": True,
    }
    assert [c["operation_id"] for c in fake.fake_assessor.calls] == [
        f"attempt-{attempt}:assess:1",
        f"attempt-{attempt}:assess:1:r1",
    ]


class Boom:
    def transcribe(self, *args, **kwargs):
        raise RuntimeError("disk on fire")


def test_unexpected_exception_is_failed_unexpected(client, world, db, settings, caplog):
    use(client, transcriber=Boom())
    rid, attempt = answered_attempt(client, world)
    post_answer(client, attempt)
    body = assert_unresolved(client, db, settings, rid, attempt)
    assert body["failure"]["kind"] == "unexpected"
    assert body["failure"]["message"] == "Something went wrong while processing. Try again."
    assert "disk on fire" not in json.dumps(body)
    assert any("failed while processing" in r.getMessage() for r in caplog.records)


def test_retry_after_assessment_failure_calls_only_assessor(client, world, db, settings):
    assessor = FakeTextGenerator({ASSESS_TASK: ai_for()}, error=provider_error("rate_limit"))
    fake = use(client, assessor=assessor)
    rid, attempt = answered_attempt(client, world)
    post_answer(client, attempt)
    assert_unresolved(client, db, settings, rid, attempt)

    assessor.error = None
    response = client.post(f"/api/attempts/{attempt}/retry")
    assert response.status_code == 202
    assert (response.json()["status"], response.json()["failure"]) == ("processing", None)
    body = result(client, attempt)
    assert (body["status"], body["assessment"]["status"]) == ("ready", "assessed")
    assert len(fake.fake_transcriber.calls) == 1
    assert [c["operation_id"] for c in assessor.calls] == [
        f"attempt-{attempt}:assess:1",
        f"attempt-{attempt}:assess:2",
    ]
    latest = client.get(f"/api/rounds/{rid}").json()["items"][0]["latest_attempt"]
    assert (latest["outcome"], latest["processing_status"]) == ("answered", "ready")
    summary = client.get(f"/api/rounds/{rid}/summary").json()
    assert summary["unresolved_attempt_ids"] == []


def test_retry_after_transcription_failure_sends_same_audio(client, world, db, settings):
    transcriber = FakeTranscriber(error=provider_error("network"))
    use(client, transcriber=transcriber)
    rid, attempt = answered_attempt(client, world)
    post_answer(client, attempt)
    assert_unresolved(client, db, settings, rid, attempt)

    transcriber.error = None
    assert client.post(f"/api/attempts/{attempt}/retry").status_code == 202
    assert result(client, attempt)["status"] == "ready"
    first, second = transcriber.calls
    assert first["audio"] == second["audio"] == wav("speech_mid.wav")
    assert [first["operation_id"], second["operation_id"]] == [
        f"attempt-{attempt}:transcribe:1",
        f"attempt-{attempt}:transcribe:2",
    ]
    db.expire_all()
    assert db.query(Recording).count() == 1


def test_no_duplicate_calls_on_repeated_posts(client, world, db):
    fake = use(client)
    _, attempt = answered_attempt(client, world)
    post_answer(client, attempt)
    sent, uploaded = post_answer(client, attempt)
    assert (sent.status_code, uploaded.status_code) == (200, 200)
    response = client.post(f"/api/attempts/{attempt}/retry")
    assert (response.status_code, response.json()["detail"]) == (
        409,
        "This attempt cannot be retried.",
    )
    assert (len(fake.fake_transcriber.calls), len(fake.fake_assessor.calls)) == (1, 1)
    db.expire_all()
    assert db.query(Recording).count() == 1


def test_second_start_while_live_is_noop(client, world, settings, monkeypatch):
    fake = use(client)
    scheduled: list[int] = []
    monkeypatch.setattr(pipeline, "schedule", lambda bg, a, s, p: scheduled.append(a))
    _, attempt = answered_attempt(client, world)
    post_answer(client, attempt)
    assert scheduled == [attempt]

    pipeline._active.add(attempt)  # a run is going on
    try:
        assert post_events(client, attempt).status_code == 200
        assert scheduled == [attempt]  # not scheduled again while live
        assert client.post(f"/api/attempts/{attempt}/retry").status_code == 409
        pipeline.run(attempt, settings, fake)
        assert result(client, attempt)["status"] == "processing"
    finally:
        pipeline._active.discard(attempt)
    assert (len(fake.fake_transcriber.calls), len(fake.fake_assessor.calls)) == (0, 0)


def test_orphaned_run_reports_interrupted_and_retry_restarts(client, world, db, monkeypatch):
    fake = use(client)
    monkeypatch.setattr(pipeline, "schedule", lambda bg, a, s, p: None)  # the run never happens
    rid, attempt = answered_attempt(client, world)
    post_answer(client, attempt)
    body = result(client, attempt)
    assert (body["status"], body["assessment"]) == ("failed", None)
    assert body["failure"] == {
        "kind": "interrupted",
        "message": "Processing was cut off. Try again.",
        "retryable": True,
    }
    db.expire_all()
    assert db.query(AttemptProcessing).one().status == "processing"  # reported, not written
    summary = client.get(f"/api/rounds/{rid}/summary").json()
    assert (summary["provisional"], summary["unresolved_attempt_ids"]) == (True, [attempt])

    monkeypatch.undo()
    assert client.post(f"/api/attempts/{attempt}/retry").status_code == 202
    body = result(client, attempt)
    assert (body["status"], body["assessment"]["status"]) == ("ready", "assessed")
    assert (len(fake.fake_transcriber.calls), len(fake.fake_assessor.calls)) == (1, 1)


def test_microphone_failure_not_retryable_409(client, world):
    fake = use(client)
    rid = start_round(client, world)["id"]
    act(client, rid, "question_ready", "question")
    attempt = act(client, rid, "mic_failure", "preparation")["current_attempt"]["id"]
    assert result(client, attempt)["failure"]["retryable"] is False
    response = client.post(f"/api/attempts/{attempt}/retry")
    assert (response.status_code, response.json()["detail"]) == (
        409,
        "This attempt cannot be retried.",
    )
    assert post_events(client, attempt).status_code == 409
    assert (fake.transcriber_builds, fake.assessor_builds) == (0, 0)
