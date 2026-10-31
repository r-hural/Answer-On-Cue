"""The retry endpoint and the main feedback held back during the follow-up stage."""

import pytest
from sqlalchemy import select
from test_attempts_support import (
    FOLLOW_UP_TASK,
    FakeProviders,
    act,
    answer_current,
    follow_up_for,
    override_providers,
    post_answer,
    start_round,
)
from test_rounds_support import Clock, make_world
from test_topics_support import passage_ids_in, upgrade

from app.db.session import SessionLocal
from app.features.follow_ups import service as follow_ups
from app.features.follow_ups.models import FollowUp
from app.features.providers.fakes import FakeTextGenerator
from app.features.providers.types import ProviderError

ON = {"enabled": True}
SERVER = ProviderError("server", "anthropic", "Fake provider failure.", retryable=True)


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    return make_world(db)


class Flaky:
    """Fails the first `failures` calls, then answers with a valid follow-up."""

    def __init__(self, failures: int = 1, before=None) -> None:
        self.failures = failures
        self.before = before
        self.calls: list[str] = []

    def generate_structured(self, task, system, messages, schema, *, operation_id, **kwargs):
        self.calls.append(operation_id)
        if self.before is not None:
            self.before()
        if len(self.calls) <= self.failures:
            raise SERVER
        inner = FakeTextGenerator({FOLLOW_UP_TASK: follow_up_for(passage_ids_in(messages))})
        return inner.generate_structured(
            task, system, messages, schema, operation_id=operation_id, **kwargs
        )


def failed_round(client, world, generator, timing="practice") -> tuple[int, int]:
    override_providers(client.app, FakeProviders(follow_up=generator))
    rid = start_round(client, world, timing, 2, follow_ups=ON)["id"]
    main = answer_current(client, rid)
    post_answer(client, main)
    return rid, main


def row_of(db) -> FollowUp:
    db.expire_all()
    return db.scalar(select(FollowUp))


def retry(client, follow_up_id: int):
    return client.post(f"/api/follow-ups/{follow_up_id}/retry")


def test_retry_after_failure_generates_and_asks(client, db, world):
    generator = Flaky()
    rid, _ = failed_round(client, world, generator)
    row = row_of(db)
    assert row.status == "failed"
    response = retry(client, row.id)
    assert response.status_code == 202, response.text
    body = response.json()
    assert (body["id"], body["status"], body["failure"]) == (row.id, "generating", None)
    row = row_of(db)
    assert (row.status, row.tries, row.failure_kind) == ("ready", 2, None)
    assert generator.calls == [
        f"follow-up-{row.id}:generate:1",
        f"follow-up-{row.id}:generate:2",
    ]
    state = client.get(f"/api/rounds/{rid}").json()
    assert (state["part"], state["phase"]) == ("followup", "question")


def test_retry_refused(client, db, world, monkeypatch):
    assert retry(client, 999).status_code == 404
    assert retry(client, 999).json()["detail"] == "Follow-up not found."

    # live-generating: the retry is refused while the call is still running
    box = {}
    generator = Flaky(failures=0, before=lambda: box.setdefault("seen", retry(client, box["id"])))
    override_providers(client.app, FakeProviders(follow_up=generator))
    rid = start_round(client, world, "practice", 2, follow_ups=ON)["id"]
    main = answer_current(client, rid)
    original = follow_ups.prepare

    def remember(session, attempt_id):
        found = original(session, attempt_id)
        if found is not None:
            box["id"] = found.id
        return found

    monkeypatch.setattr(follow_ups, "prepare", remember)
    post_answer(client, main)
    monkeypatch.undo()
    assert box["seen"].status_code == 409
    assert box["seen"].json()["detail"] == "This follow-up cannot be retried."
    ready = row_of(db)
    assert ready.status == "ready"
    assert retry(client, ready.id).status_code == 409  # ready

    # omitted
    rid, _ = failed_round(client, world, Flaky(failures=5))
    act(client, rid, "no_follow_up", "processing")
    omitted = db.scalar(select(FollowUp).where(FollowUp.status == "omitted"))
    assert retry(client, omitted.id).status_code == 409


def test_retry_refused_after_the_round_moved_on(client, db, world):
    rid, _ = failed_round(client, world, Flaky(failures=5))
    row = row_of(db)
    act(client, rid, "abandon", "processing")
    assert retry(client, row.id).status_code == 409
    assert row_of(db).status == "failed"


def test_stale_generating_can_be_retried(client, db, world, monkeypatch):
    monkeypatch.setattr(follow_ups, "generate_pending", lambda *a, **k: None)
    rid, _ = failed_round(client, world, Flaky(failures=0))
    row = row_of(db)
    follow_ups.unschedule(row.id)  # the process that prepared it is gone
    state = client.get(f"/api/rounds/{rid}").json()
    assert state["follow_up"]["failure"]["kind"] == "interrupted"
    assert row_of(db).status == "generating"  # no write until the POST
    monkeypatch.undo()
    assert retry(client, row.id).status_code == 202
    row = row_of(db)
    assert (row.status, row.tries) == ("ready", 1)
    assert client.get(f"/api/rounds/{rid}").json()["part"] == "followup"


def test_retry_resets_wait_window(client, db, world, monkeypatch):
    """Failed at t0, retried at t0 + 200 s → the later result is stored ready."""
    clock = Clock()
    monkeypatch.setattr(follow_ups, "now", clock)
    rid, _ = failed_round(client, world, Flaky())
    row = row_of(db)
    clock.advance(seconds=200)
    assert retry(client, row.id).status_code == 202
    row = row_of(db)
    assert row.status == "ready"
    assert client.get(f"/api/rounds/{rid}").json()["part"] == "followup"


def _withheld_everything(client, main: int) -> None:
    body = client.get(f"/api/attempts/{main}/result").json()
    assert body["withheld"] is True
    assert (body["assessment"], body["transcript"], body["metrics"]) == (None, None, None)
    report = client.get(f"/api/reports/attempts/{main}").json()
    assert (report["points"], report["model_answer"], report["sources"]) == ([], None, [])
    assert report["key_points"] == []


def test_main_result_withheld_during_follow_up_stage(client, db, world):
    box = {}

    def look(messages):
        box["during"] = client.get(f"/api/attempts/{box['main']}/result").json()
        return follow_up_for(passage_ids_in(messages))

    override_providers(
        client.app, FakeProviders(follow_up=FakeTextGenerator({FOLLOW_UP_TASK: look}))
    )
    rid = start_round(client, world, "practice", 2, follow_ups=ON)["id"]
    box["main"] = main = answer_current(client, rid)
    post_answer(client, main)
    assert box["during"]["withheld"] is True and box["during"]["assessment"] is None
    _withheld_everything(client, main)  # the follow-up question is being asked
    follow = answer_current(client, rid)
    _withheld_everything(client, main)  # the follow-up is being processed
    assert client.get(f"/api/attempts/{follow}/result").json()["withheld"] is False
    post_answer(client, follow)
    assert client.get(f"/api/rounds/{rid}").json()["phase"] == "feedback"
    body = client.get(f"/api/attempts/{main}/result").json()
    assert body["withheld"] is False and body["assessment"]["status"] == "assessed"
    report = client.get(f"/api/reports/attempts/{main}").json()
    assert report["points"] and report["sources"]


def test_main_result_withheld_while_follow_up_failed(client, db, world):
    rid, main = failed_round(client, world, Flaky(failures=5))
    _withheld_everything(client, main)
    act(client, rid, "no_follow_up", "processing")
    assert client.get(f"/api/attempts/{main}/result").json()["withheld"] is False


def test_follow_up_result_withheld_in_exam_until_end(client, db, world):
    override_providers(client.app, FakeProviders())
    rid = start_round(client, world, "exam", 1, follow_ups=ON)["id"]
    main = answer_current(client, rid)
    post_answer(client, main)
    state = client.get(f"/api/rounds/{rid}").json()
    assert (state["part"], state["phase"]) == ("followup", "question")
    follow = answer_current(client, rid)
    assert client.get(f"/api/rounds/{rid}").json()["status"] == "finished"
    post_answer(client, follow)
    body = client.get(f"/api/attempts/{follow}/result").json()
    assert body["withheld"] is False and body["assessment"]["status"] == "assessed"

    # a second, still running exam round withholds its follow-up result
    rid = start_round(client, world, "exam", 2, follow_ups=ON)["id"]
    post_answer(client, answer_current(client, rid))
    follow = answer_current(client, rid)
    post_answer(client, follow)
    body = client.get(f"/api/attempts/{follow}/result").json()
    assert body["withheld"] is True and body["assessment"] is None


@pytest.mark.parametrize("enabled", [True, False])
def test_main_result_withheld_between_ready_and_prepare(client, db, world, monkeypatch, enabled):
    """The pipeline commits the ready result, then prepares the follow-up and marks the attempt:
    a read in between must not disclose a main answer whose follow-up may still come."""
    box = {}
    prepare = follow_ups.prepare

    def look_first(session, attempt_id):
        box["result"] = client.get(f"/api/attempts/{attempt_id}/result").json()
        box["report"] = client.get(f"/api/reports/attempts/{attempt_id}").json()
        return prepare(session, attempt_id)

    monkeypatch.setattr(follow_ups, "prepare", look_first)
    override_providers(client.app, FakeProviders())
    rid = start_round(client, world, "practice", 2, follow_ups={"enabled": enabled})["id"]
    main = answer_current(client, rid)
    post_answer(client, main)
    result, report = box["result"], box["report"]
    assert result["status"] == "ready"
    if enabled:
        assert result["withheld"] is True
        assert (result["assessment"], result["transcript"], result["metrics"]) == (None, None, None)
        assert (report["points"], report["model_answer"], report["sources"]) == ([], None, [])
        assert client.get(f"/api/rounds/{rid}").json()["part"] == "followup"
    else:  # no follow-up can come: nothing to wait for
        assert result["withheld"] is False and result["assessment"] is not None
