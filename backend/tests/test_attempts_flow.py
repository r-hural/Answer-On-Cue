"""Whole rounds through the API only, with fakes behind the provider seam."""

import pytest
from test_attempts_support import (
    FakeProviders,
    act,
    answer_current,
    override_providers,
    post_answer,
    result,
    start_round,
)
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.providers.fakes import FakeTranscriber
from app.features.providers.types import ProviderError


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    return make_world(db)


class FailsOnCall(FakeTranscriber):
    """Fails exactly once, on the given call number (1-based)."""

    def __init__(self, failing_call: int) -> None:
        super().__init__()
        self.failing_call = failing_call

    def transcribe(self, *args, **kwargs):
        self.error = (
            ProviderError("timeout", "openai", "The provider did not respond in time.")
            if len(self.calls) + 1 == self.failing_call
            else None
        )
        return super().transcribe(*args, **kwargs)


def test_full_round_through_pipeline(client, world):
    fake = FakeProviders(transcriber=FailsOnCall(failing_call=2))
    override_providers(client.app, fake)
    rid = start_round(client, world, "exam", count=3)["id"]

    q1 = answer_current(client, rid)  # the exam moves on at once
    post_answer(client, q1)
    body = result(client, q1)
    assert (body["status"], body["withheld"]) == ("ready", True)
    assert (body["transcript"], body["metrics"], body["assessment"]) == (None, None, None)

    act(client, rid, "question_ready", "question")
    q2 = act(client, rid, "dont_know", "preparation")["items"][1]["latest_attempt"]["id"]
    assert result(client, q2)["assessment"] is None  # withheld too

    q3 = answer_current(client, rid)
    assert client.get(f"/api/rounds/{rid}").json()["status"] == "finished"
    post_answer(client, q3)

    first = result(client, q1)
    assert (first["status"], first["withheld"]) == ("ready", False)
    assert first["assessment"]["status"] == "assessed" and first["assessment"]["band"]
    assert first["transcript"] == "To jest testowa odpowiedź."
    second = result(client, q2)
    assert (second["status"], second["assessment"]["status"]) == ("ready", "no_answer")
    assert second["assessment"]["band"] == "weak"
    third = result(client, q3)
    assert (third["status"], third["failure"]["kind"], third["assessment"]) == (
        "failed",
        "timeout",
        None,
    )
    summary = client.get(f"/api/rounds/{rid}/summary").json()
    assert (summary["provisional"], summary["unresolved_attempt_ids"]) == (True, [q3])
    assert summary["outcomes"] == {"answered": 1, "dont_know": 1, "technical_failure": 1}

    assert client.post(f"/api/attempts/{q3}/retry").status_code == 202
    third = result(client, q3)
    assert (third["status"], third["assessment"]["status"]) == ("ready", "assessed")
    summary = client.get(f"/api/rounds/{rid}/summary").json()
    assert (summary["provisional"], summary["unresolved_attempt_ids"]) == (False, [])
    assert summary["outcomes"] == {"answered": 2, "dont_know": 1}
    assert len(fake.fake_transcriber.calls) == 3
    assert len(fake.fake_assessor.calls) == 2


def test_full_practice_attempt_reaches_feedback(client, world):
    fake = FakeProviders()
    override_providers(client.app, fake)
    rid = start_round(client, world, count=2)["id"]
    attempt = answer_current(client, rid)
    assert client.get(f"/api/rounds/{rid}").json()["phase"] == "processing"
    pending = result(client, attempt)
    assert (pending["status"], pending["stage"]) == ("processing", "waiting_for_audio")
    post_answer(client, attempt)
    state = client.get(f"/api/rounds/{rid}").json()
    assert state["phase"] == "feedback"
    body = result(client, attempt)
    assert (body["status"], body["withheld"]) == ("ready", False)
    assert body["assessment"]["band"] is not None
    state = act(client, rid, "next", "feedback")
    assert (state["phase"], state["current_index"]) == ("question", 1)
