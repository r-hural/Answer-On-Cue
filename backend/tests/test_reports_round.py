from datetime import UTC, datetime, timedelta

import pytest
from test_attempts_flow import FailsOnCall
from test_attempts_support import (
    FakeProviders,
    act,
    ai_for,
    answer_current,
    answered_attempt,
    override_providers,
    post_answer,
    start_round,
)
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.providers.fakes import FakeTextGenerator
from app.features.rounds.models import Attempt

ASSESS_TASK = "assessment.assess"


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


def report(client, round_id: int) -> dict:
    response = client.get(f"/api/reports/rounds/{round_id}")
    assert response.status_code == 200, response.text
    return response.json()


def test_round_report_scores(client, world):
    use(client, transcriber=FailsOnCall(failing_call=2))
    rid = start_round(client, world, count=3)["id"]
    q1 = answer_current(client, rid)
    post_answer(client, q1)
    act(client, rid, "next", "feedback")
    act(client, rid, "question_ready", "question")
    act(client, rid, "dont_know", "preparation")
    act(client, rid, "next", "feedback")
    q3 = answer_current(client, rid)
    post_answer(client, q3)  # the transcriber fails

    body = report(client, rid)
    first, second, third = body["items"]
    assert [i["ordinal"] for i in body["items"]] == [1, 2, 3]
    assert first["status"] == "ready" and first["band"] is not None and first["total"] > 0
    assert (second["outcome"], second["status"], second["band"]) == ("dont_know", "ready", "weak")
    assert second["total"] == 0
    assert (third["status"], third["band"], third["total"]) == ("failed", None, None)
    assert third["failure"]["kind"] == "timeout"
    knowledge = body["knowledge"]
    assert knowledge["primary_score"] == pytest.approx(first["total"] / 2)
    assert knowledge["unresolved"] == 1 and knowledge["provisional"] is True
    assert sum(knowledge["band_distribution"].values()) == 2
    assert body["provisional"] is True
    assert (body["asked"], body["total"], body["withheld"]) == (3, 3, False)
    assert body["subject_name"] == "Historia"
    assert body["config"]["feedback_timing"] == "practice"

    delivery = body["delivery"]
    assert delivery["count_included"] == 1  # only the answered, assessed one has metrics


def test_delivery_unavailable_not_zero(client, world):
    use(client)
    rid, attempt = answered_attempt(client, world)
    post_answer(client, attempt, "silence.wav", start_reason="button")
    body = report(client, rid)
    row = body["items"][0]
    assert row["status"] == "ready" and row["band"] is None and row["total"] is None
    assert row["speech_start_latency_ms"] is None and row["fluency"] is None
    assert body["knowledge"]["primary_score"] is None
    assert body["knowledge"]["no_speech"] == 1 and body["provisional"] is True
    delivery = body["delivery"]
    assert delivery["median_speech_start_latency_ms"] is None
    assert delivery["median_words_per_minute"] is None
    assert delivery["target_attainment"]["speech_start_latency_ms"] == [0, 0]


def test_restarted_and_interruptions(client, world):
    use(client)
    rid = start_round(client, world, count=2)["id"]
    act(client, rid, "question_ready", "question")
    act(client, rid, "start_answer", "preparation", start_reason="auto")
    act(client, rid, "pause", "recording")
    act(client, rid, "resume", "recording")
    attempt = answer_current(client, rid)
    post_answer(client, attempt)
    body = report(client, rid)
    assert body["restarted_ordinals"] == [1]
    assert body["items"][0]["restarted"] is True and body["items"][0]["status"] == "ready"
    assert body["items"][0]["attempt_id"] == attempt
    assert body["pause_count"] == 1
    assert body["items"][1]["restarted"] is False


def test_not_asked_rows_for_abandoned_round(client, world):
    use(client)
    rid = start_round(client, world, count=3)["id"]
    q1 = answer_current(client, rid)
    post_answer(client, q1)
    act(client, rid, "next", "feedback")
    act(client, rid, "abandon", "question")
    body = report(client, rid)
    assert body["status"] == "abandoned" and body["provisional"] is True
    assert [i["status"] for i in body["items"]] == ["ready", "not_asked", "not_asked"]
    assert body["asked"] == 1 and body["total"] == 3
    assert body["items"][1]["attempt_id"] is None and body["items"][1]["band"] is None
    assert body["knowledge"]["unresolved"] == 0


def test_withheld_exam_round_report(client, world):
    use(client)
    rid = start_round(client, world, "exam", count=2)["id"]
    q1 = answer_current(client, rid)
    post_answer(client, q1)
    body = report(client, rid)
    assert body["withheld"] is True and body["status"] == "active"
    assert body["knowledge"] is None and body["delivery"] is None
    assert all(
        i["band"] is None and i["total"] is None and i["fluency"] is None for i in body["items"]
    )
    assert body["items"][0]["attempt_id"] == q1
    q2 = answer_current(client, rid)
    post_answer(client, q2)
    body = report(client, rid)
    assert body["withheld"] is False and body["status"] == "finished"
    assert body["knowledge"]["primary_score"] is not None
    assert all(i["band"] is not None for i in body["items"])


def test_audio_missing_settled_by_round_report(client, world, db):
    use(client)
    rid, attempt = answered_attempt(client, world)
    row = db.get(Attempt, attempt)
    row.ended_at = datetime.now(UTC) - timedelta(seconds=300)
    db.commit()
    body = report(client, rid)
    item = body["items"][0]
    assert item["status"] == "failed" and item["band"] is None
    assert item["failure"]["kind"] == "audio_missing"
    assert body["provisional"] is True and body["knowledge"]["unresolved"] == 1


def test_unknown_round_404(client, world):
    assert client.get("/api/reports/rounds/999").status_code == 404


def test_not_asked_without_assessor_call(client, world):
    fake = use(client, assessor=FakeTextGenerator({ASSESS_TASK: ai_for()}))
    rid = start_round(client, world, count=2)["id"]
    report(client, rid)
    assert fake.fake_assessor.calls == []
