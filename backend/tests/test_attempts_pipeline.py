import pytest
from test_attempts_support import (
    ASSESS_TASK,
    FakeProviders,
    ai_for,
    answer_current,
    answered_attempt,
    override_providers,
    post_answer,
    result,
    start_round,
)
from test_rounds_support import make_world
from test_topics_support import make_question, make_subject, make_topic, make_version, upgrade

from app.db.session import SessionLocal
from app.features.assessment.scoring import classify_band
from app.features.attempts import pipeline
from app.features.attempts.models import AttemptProcessing
from app.features.providers.fakes import FakeTextGenerator, FakeTranscriber


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


def test_stages_store_outputs_and_mark_ready(client, world, db, fake):
    rid, attempt = answered_attempt(client, world)
    assert client.get(f"/api/rounds/{rid}").json()["phase"] == "processing"
    sent, uploaded = post_answer(client, attempt)
    assert (sent.status_code, uploaded.status_code) == (200, 201)
    body = result(client, attempt)
    assert (body["status"], body["stage"], body["outcome"]) == ("ready", "done", "answered")
    assert body["transcript"] == "To jest testowa odpowiedź."
    assert body["metrics"]["speech_ms"] > 0
    assert body["metrics"]["words"] == 4
    assert body["assessment"]["status"] == "assessed"
    assert body["assessment"]["band"] is not None
    assert body["failure"] is None and body["withheld"] is False
    state = client.get(f"/api/rounds/{rid}").json()
    assert state["phase"] == "feedback"
    assert state["current_attempt"]["processing_status"] == "ready"
    db.expire_all()
    row = db.query(AttemptProcessing).one()
    assert (row.transcribe_tries, row.assess_tries) == (1, 1)
    assert '"provider":"openai"' in row.transcript_meta_json.replace(" ", "")
    assert (len(fake.fake_transcriber.calls), len(fake.fake_assessor.calls)) == (1, 1)
    assert fake.fake_transcriber.calls[0]["mime"] == "audio/wav"


def test_glossary_from_topic_terms(client, db, fake):
    subject = make_subject(db)
    _, passages = make_version(db, subject.id, count=2, excluded=())
    athens = make_topic(db, subject.id, "Ateny", passages[:1], terms=("ostracyzm", "eklezja"))
    sparta = make_topic(db, subject.id, "Sparta", passages[1:], terms=("helota",), ordinal=1)
    make_question(db, subject.id, athens.id, passage_ids=(passages[0].id,))
    make_question(db, subject.id, sparta.id, "Omów ustrój Sparty.", passage_ids=(passages[1].id,))
    rid = start_round(
        client, {"subject": subject}, scope={"kind": "topics", "topic_ids": [athens.id]}
    )["id"]
    attempt = answer_current(client, rid)
    post_answer(client, attempt)
    glossary = fake.fake_transcriber.calls[0]["glossary"]
    assert glossary == ["ostracyzm", "eklezja"]
    sent = " ".join(glossary)
    for secret in ("Zgromadzenie ludowe", "Rada Pięciuset", "Demokracja ateńska", "Fragment"):
        assert secret not in sent


def test_glossary_subject_wide_without_topic(client, db, fake):
    subject = make_subject(db)
    _, passages = make_version(db, subject.id, count=1, excluded=())
    make_topic(db, subject.id, "Ateny", passages, terms=("ostracyzm",))
    make_question(db, subject.id, None, passage_ids=(passages[0].id,))
    rid = start_round(client, {"subject": subject})["id"]
    post_answer(client, answer_current(client, rid))
    assert fake.fake_transcriber.calls[0]["glossary"] == ["ostracyzm"]


def test_operation_ids_per_stage(client, world, fake):
    _, attempt = answered_attempt(client, world)
    post_answer(client, attempt)
    assert [c["operation_id"] for c in fake.fake_transcriber.calls] == [
        f"attempt-{attempt}:transcribe:1"
    ]
    assert [c["operation_id"] for c in fake.fake_assessor.calls] == [f"attempt-{attempt}:assess:1"]


def test_silence_is_no_speech_not_weak(client, world, fake):
    rid, attempt = answered_attempt(client, world)
    post_answer(client, attempt, audio="silence.wav")
    body = result(client, attempt)
    assert (body["status"], body["stage"]) == ("ready", "done")
    assessment = body["assessment"]
    assert assessment["status"] == "no_speech"
    assert assessment["band"] is None and assessment["scores"] is None
    assert body["transcript"] is None
    assert body["metrics"]["speech_ms"] == 0
    assert (fake.fake_transcriber.calls, fake.fake_assessor.calls) == ([], [])
    assert (fake.transcriber_builds, fake.assessor_builds) == (0, 0)
    assert client.get(f"/api/rounds/{rid}").json()["phase"] == "feedback"


def test_letterless_transcript_is_no_speech(client, world, db):
    fake = FakeProviders(transcriber=FakeTranscriber(text="..."))
    override_providers(client.app, fake)
    _, attempt = answered_attempt(client, world)
    post_answer(client, attempt)
    body = result(client, attempt)
    assert body["status"] == "ready"
    assert (body["assessment"]["status"], body["assessment"]["band"]) == ("no_speech", None)
    assert body["transcript"] == "..."
    assert len(fake.fake_transcriber.calls) == 1
    assert fake.assessor_builds == 0
    db.expire_all()
    assert db.query(AttemptProcessing).one().assess_tries == 0


def test_band_uses_round_snapshot(client, world, settings):
    fake = FakeProviders(
        assessor=FakeTextGenerator({ASSESS_TASK: ai_for(statuses=["covered", "partial"])})
    )
    override_providers(client.app, fake)
    rid, attempt = answered_attempt(client, world)
    snapshot = tuple(client.get(f"/api/rounds/{rid}").json()["config"]["band_bounds"])
    assert snapshot == (30, 60, 80)
    client.app.state.settings = settings.model_copy(update={"factual_band_bounds": (90, 95, 99)})
    post_answer(client, attempt)
    assessment = result(client, attempt)["assessment"]
    factual = assessment["scores"]["factual"]
    assert tuple(assessment["band_bounds"]) == snapshot
    assert assessment["band"] == classify_band(factual, snapshot)
    assert assessment["band"] != classify_band(factual, (90, 95, 99))


def test_run_is_noop_when_not_processing_or_active(client, world, db, settings, fake):
    _, attempt = answered_attempt(client, world)
    post_answer(client, attempt)
    calls = (len(fake.fake_transcriber.calls), len(fake.fake_assessor.calls))
    assert calls == (1, 1)
    pipeline.run(attempt, settings, fake)  # ready: nothing to do
    pipeline.run(999, settings, fake)  # no processing row
    assert (len(fake.fake_transcriber.calls), len(fake.fake_assessor.calls)) == calls

    db.expire_all()
    row = db.query(AttemptProcessing).one()
    row.status, row.transcript_text, row.assessment_json = "processing", None, None
    db.commit()
    pipeline._active.add(attempt)  # another run of this attempt is going on
    try:
        pipeline.run(attempt, settings, fake)
    finally:
        pipeline._active.discard(attempt)
    assert (len(fake.fake_transcriber.calls), len(fake.fake_assessor.calls)) == calls
    assert not pipeline.is_live(attempt)
