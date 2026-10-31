from datetime import UTC, datetime, timedelta

import pytest
from test_attempts_failures import band_values
from test_attempts_support import (
    FakeProviders,
    act,
    ai_for,
    answer_current,
    answered_attempt,
    override_providers,
    post_answer,
    result,
    start_round,
)
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.providers.fakes import FakeTextGenerator, FakeTranscriber
from app.features.providers.types import ProviderError
from app.features.reports.models import TranscriptReport
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


def use(client, statuses=None, **fakes) -> FakeProviders:
    assessor = FakeTextGenerator({ASSESS_TASK: ai_for(statuses=statuses)})
    fake = FakeProviders(assessor=assessor, **fakes)
    override_providers(client.app, fake)
    return fake


def report(client, attempt_id: int) -> dict:
    response = client.get(f"/api/reports/attempts/{attempt_id}")
    assert response.status_code == 200, response.text
    return response.json()


def test_assessed_answer_report(client, world):
    use(client)
    rid, attempt = answered_attempt(client, world, count=2)
    post_answer(client, attempt)
    body = report(client, attempt)
    assert body["result"] == result(client, attempt)
    assert (body["ordinal"], body["total"], body["round_id"]) == (1, 2, rid)
    assert body["subject_name"] == "Historia"
    assert (body["feedback_timing"], body["round_status"]) == ("practice", "active")
    assert body["question_text"].startswith("Pytanie")
    assert [(p["id"], p["text"], p["status"]) for p in body["points"]] == [
        ("p1", "Zgromadzenie ludowe", "covered"),
        ("p2", "Rada Pięciuset", "covered"),
    ]
    assert body["points"][0]["note"] == "Uwaga."
    assert body["sources"]
    assert {s["document_title"] for s in body["sources"]} == {"Podręcznik"}
    assert all(len(s["excerpt"]) <= 240 and s["locator"] for s in body["sources"])
    assert len({s["passage_id"] for s in body["sources"]}) == len(body["sources"])
    assert body["transcript_report"] is None


def test_model_answer_rule(client, world):
    use(client, statuses=["missing", "missing"])
    rid, weak = answered_attempt(client, world, count=2)
    post_answer(client, weak)
    body = report(client, weak)
    assert body["result"]["assessment"]["band"] == "weak"
    assert body["model_answer_display"] == "auto"
    assert body["model_answer"] == "Demokracja ateńska opierała się na zgromadzeniu i radzie."
    assert body["key_points"] == ["Zgromadzenie ludowe", "Rada Pięciuset"]

    use(client)  # all points covered
    rid, good = answered_attempt(client, world)
    post_answer(client, good)
    body = report(client, good)
    assert body["result"]["assessment"]["band"] in ("good", "very_good")
    assert body["model_answer_display"] == "on_request"
    assert body["model_answer"] and body["key_points"] == []

    rid = start_round(client, world, count=2)["id"]
    act(client, rid, "question_ready", "question")
    dk = act(client, rid, "dont_know", "preparation")["items"][0]["latest_attempt"]["id"]
    body = report(client, dk)
    assert body["result"]["assessment"]["status"] == "no_answer"
    assert body["model_answer_display"] == "auto"
    assert body["key_points"] == ["Zgromadzenie ludowe", "Rada Pięciuset"]
    assert body["points"] == []


def test_failed_and_no_speech_have_no_model_answer_or_points(client, world, db):
    use(client, transcriber=FakeTranscriber(error=ProviderError("timeout", "openai", "Slow.")))
    rid, failed = answered_attempt(client, world)
    post_answer(client, failed)
    body = report(client, failed)
    assert body["result"]["status"] == "failed"
    for key in ("model_answer", "expanded_explanation", "model_answer_display"):
        assert body[key] is None
    assert body["points"] == [] and body["key_points"] == [] and body["sources"] == []
    assert band_values(body) == []

    use(client)
    rid, silent = answered_attempt(client, world)
    post_answer(client, silent, "silence.wav")
    body = report(client, silent)
    assert body["result"]["assessment"]["status"] == "no_speech"
    assert body["model_answer"] is None and body["points"] == []
    assert band_values(body) == []

    # a missing recording is a failure too
    use(client)
    rid, missing = answered_attempt(client, world)
    attempt = db.get(Attempt, missing)
    attempt.ended_at = datetime.now(UTC) - timedelta(seconds=300)
    db.commit()
    body = report(client, missing)
    assert body["result"]["failure"]["kind"] == "audio_missing"
    assert body["model_answer"] is None and band_values(body) == []


@pytest.mark.parametrize("pause", [False, True])
def test_exam_round_withheld_until_end(client, world, pause):
    use(client, statuses=["missing", "missing"])
    rid = start_round(client, world, "exam", count=2)["id"]
    q1 = answer_current(client, rid)
    post_answer(client, q1)
    if pause:
        act(client, rid, "pause", "question")
    body = report(client, q1)
    assert body["result"]["withheld"] is True
    assert body["points"] == [] and body["key_points"] == [] and body["sources"] == []
    for key in ("model_answer", "expanded_explanation", "model_answer_display"):
        assert body[key] is None
    assert body["result"]["transcript"] is None and body["result"]["assessment"] is None
    assert (
        client.put(
            f"/api/reports/attempts/{q1}/transcript-report", json={"corrected_text": "x"}
        ).status_code
        == 409
    )
    if pause:
        act(client, rid, "resume", "question")
    q2 = answer_current(client, rid)
    post_answer(client, q2)
    body = report(client, q1)
    assert body["result"]["withheld"] is False
    assert len(body["points"]) == 2 and body["model_answer"] and body["sources"]


def test_transcript_report_put_and_replace(client, world, db):
    use(client)
    rid, attempt = answered_attempt(client, world)
    post_answer(client, attempt)
    url = f"/api/reports/attempts/{attempt}/transcript-report"
    first = client.put(url, json={"corrected_text": "  Pierwsza wersja.  "})
    assert first.status_code == 200
    assert first.json()["corrected_text"] == "Pierwsza wersja."
    assert first.json()["attempt_id"] == attempt
    second = client.put(url, json={"corrected_text": "Druga wersja."})
    assert second.status_code == 200
    assert db.query(TranscriptReport).count() == 1
    assert report(client, attempt)["transcript_report"]["corrected_text"] == "Druga wersja."
    assert client.put(url, json={"corrected_text": "   "}).status_code == 422
    assert client.put(url, json={"corrected_text": "x" * 20001}).status_code == 422

    rid = start_round(client, world, count=2)["id"]
    act(client, rid, "question_ready", "question")
    dk = act(client, rid, "dont_know", "preparation")["items"][0]["latest_attempt"]["id"]
    put = client.put(f"/api/reports/attempts/{dk}/transcript-report", json={"corrected_text": "x"})
    assert (put.status_code, put.json()["detail"]) == (409, "There is no transcript to report on.")

    use(client, transcriber=FakeTranscriber(error=ProviderError("timeout", "openai", "Slow.")))
    rid, failed = answered_attempt(client, world)
    post_answer(client, failed)
    put = client.put(
        f"/api/reports/attempts/{failed}/transcript-report", json={"corrected_text": "x"}
    )
    assert put.status_code == 409


def test_unknown_and_pending_attempts(client, world):
    assert client.get("/api/reports/attempts/999").status_code == 404
    rid = start_round(client, world)["id"]
    attempt = client.get(f"/api/rounds/{rid}").json()["current_attempt"]["id"]
    response = client.get(f"/api/reports/attempts/{attempt}")
    assert (response.status_code, response.json()["detail"]) == (
        409,
        "This attempt has no answer yet.",
    )
