"""Reports: the follow-up brief, the follow-up report and the series, kept apart from the score."""

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from test_attempts_support import (
    ASSESS_TASK,
    FOLLOW_UP_TASK,
    FOLLOW_UP_TEXT,
    FakeProviders,
    act,
    ai_for,
    answer_current,
    override_providers,
    post_answer,
    start_round,
)
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.follow_ups.models import FollowUp
from app.features.providers.fakes import FakeTextGenerator, FakeTranscriber
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


def graded(main=("covered", "covered"), follow=("covered", "covered")) -> FakeTextGenerator:
    """Main answers and follow-up answers judged apart (the follow-up text is in its prompt)."""

    def judge(messages):
        statuses = follow if FOLLOW_UP_TEXT in messages[0].content else main
        return ai_for(statuses=list(statuses))

    return FakeTextGenerator({ASSESS_TASK: judge})


def use(client, **fakes) -> FakeProviders:
    fake = FakeProviders(**fakes)
    override_providers(client.app, fake)
    return fake


def get(client, path: str) -> dict:
    response = client.get(f"/api{path}")
    assert response.status_code == 200, response.text
    return response.json()


def main_and_follow_up(client, world, timing="practice", count=1, **config):
    """One main answer and its answered follow-up; returns (round, main, follow-up)."""
    config.setdefault("follow_ups", ON)
    rid = start_round(client, world, timing, count, **config)["id"]
    main = answer_current(client, rid)
    post_answer(client, main)
    follow = answer_current(client, rid)
    post_answer(client, follow)
    return rid, main, follow


def test_main_report_follow_up_brief(client, world):
    use(client, assessor=graded(follow=("covered", "missing")))
    rid, main, follow = main_and_follow_up(client, world)
    body = get(client, f"/reports/attempts/{main}")
    assert (body["role"], body["parent_attempt_id"]) == ("main", None)
    brief = body["follow_up"]
    assert (brief["asked"], brief["reason"], brief["attempt_id"]) == (True, None, follow)
    assert (brief["question_text"], brief["outcome"], brief["status"]) == (
        FOLLOW_UP_TEXT,
        "answered",
        "ready",
    )
    assert brief["band"] is not None and brief["factual"] is not None
    assert brief["note"] == "Uwaga. Uwaga."

    use(client)
    rid = start_round(client, world, "practice", 1)["id"]
    main = answer_current(client, rid)
    post_answer(client, main)
    assert get(client, f"/reports/attempts/{main}")["follow_up"] is None


def test_follow_up_report(client, world):
    use(client, assessor=graded())
    rid, main, follow = main_and_follow_up(client, world, count=2)
    body = get(client, f"/reports/attempts/{follow}")
    main_body = get(client, f"/reports/attempts/{main}")
    assert (body["role"], body["parent_attempt_id"], body["follow_up"]) == ("followup", main, None)
    assert body["ordinal"] == main_body["ordinal"] == 1
    assert body["total"] == 2
    assert body["question_text"] == FOLLOW_UP_TEXT
    assert [p["text"] for p in body["points"]] == [
        "Ochrona przed tyranią",
        "Wygnanie na dziesięć lat",
    ]
    assert body["model_answer"] is not None  # its own rule: Very good shows it
    assert body["sources"]


def _reason(client, main: int) -> dict:
    return get(client, f"/reports/attempts/{main}")["follow_up"]


def test_not_asked_reasons(client, world):
    use(client)
    rid = start_round(client, world, "practice", 1, follow_ups=ON)["id"]
    act(client, rid, "question_ready", "question")
    main = act(client, rid, "dont_know", "preparation")["current_attempt"]["id"]
    assert (_reason(client, main)["asked"], _reason(client, main)["reason"]) == (False, "no_answer")
    assert _reason(client, main)["band"] is None

    use(client, transcriber=FakeTranscriber(error=SERVER))
    rid = start_round(client, world, "practice", 1, follow_ups=ON)["id"]
    main = answer_current(client, rid)
    post_answer(client, main)
    assert _reason(client, main)["reason"] == "main_unresolved"

    use(client, follow_up=FakeTextGenerator({}, error=SERVER))
    rid = start_round(client, world, "practice", 1, follow_ups=ON)["id"]
    main = answer_current(client, rid)
    post_answer(client, main)
    act(client, rid, "no_follow_up", "processing")
    brief = _reason(client, main)
    assert (brief["reason"], brief["failure_message"]) == (
        "generation_failed",
        "Fake provider failure.",
    )

    box = {}

    def continue_first(messages):
        act(client, box["rid"], "no_follow_up", "processing")
        raise SERVER

    use(client, follow_up=FakeTextGenerator({FOLLOW_UP_TASK: continue_first}))
    box["rid"] = start_round(client, world, "practice", 1, follow_ups=ON)["id"]
    main = answer_current(client, box["rid"])
    post_answer(client, main)
    assert _reason(client, main)["reason"] == "continued"

    use(client)
    rid = start_round(client, world, "practice", 1, follow_ups=ON)["id"]
    main = answer_current(client, rid)
    post_answer(client, main)
    act(client, rid, "abandon", "question")
    assert _reason(client, main)["reason"] == "not_reached"


def _scores(report: dict) -> tuple:
    knowledge = report["knowledge"]
    return (
        knowledge["primary_score"],
        knowledge["band_distribution"],
        knowledge["mean_correctness"],
        knowledge["mean_completeness"],
        knowledge["mean_factual"],
        knowledge["mean_language"],
        knowledge["provisional"],
        report["delivery"],
        report["provisional"],
    )


def test_follow_up_never_changes_primary_score(client, world):
    use(client, assessor=graded(main=("missing", "missing"), follow=("covered", "covered")))
    with_follow, main, follow = main_and_follow_up(client, world)
    act(client, with_follow, "next", "feedback")
    rid = start_round(client, world, "practice", 1)["id"]  # the same answer, follow-ups off
    post_answer(client, answer_current(client, rid))
    act(client, rid, "next", "feedback")

    a = get(client, f"/reports/rounds/{with_follow}")
    b = get(client, f"/reports/rounds/{rid}")
    assert a["knowledge"]["band_distribution"]["weak"] == 1
    assert _scores(a) == _scores(b)
    assert [i["attempt_id"] for i in a["items"]] == [main]
    series = a["follow_ups"]
    assert series["knowledge"]["band_distribution"]["very_good"] == 1
    assert series["knowledge"]["mean_factual"] > a["knowledge"]["mean_factual"]
    assert b["follow_ups"] is None
    rows = {r["round_id"]: r for r in get(client, "/reports/history")}
    assert rows[with_follow]["primary_score"] == rows[rid]["primary_score"]
    assert rows[with_follow]["band_distribution"] == rows[rid]["band_distribution"]
    assert get(client, f"/reports/attempts/{main}")["follow_up"]["attempt_id"] == follow


def test_follow_up_series(client, world):
    calls = {"n": 0}

    class Transcriber(FakeTranscriber):
        def transcribe(self, *args, **kwargs):
            calls["n"] += 1
            self.error = SERVER if calls["n"] == 5 else None  # the third follow-up's answer
            return super().transcribe(*args, **kwargs)

    use(client, assessor=graded(follow=("covered", "partial")), transcriber=Transcriber())
    rid = start_round(client, world, "practice", 3, follow_ups=ON)["id"]
    mains = []
    for how in ("answer", "dont_know", "answer"):
        mains.append(answer_current(client, rid))
        post_answer(client, mains[-1])
        if how == "answer":
            post_answer(client, answer_current(client, rid))
        else:
            act(client, rid, "question_ready", "question")
            act(client, rid, "dont_know", "preparation")
        act(client, rid, "next", "feedback")
    body = get(client, f"/reports/rounds/{rid}")
    assert body["status"] == "finished"
    series = body["follow_ups"]
    assert (series["asked"], series["not_asked"], series["unresolved"]) == (3, 0, 1)
    assert series["good_or_better"] == 1
    assert series["provisional"] is True
    knowledge = series["knowledge"]
    assert (knowledge["assessed"], knowledge["no_answer"], knowledge["unresolved"]) == (1, 1, 1)
    good = _reason(client, mains[0])
    assert good["band"] == "good"
    assert knowledge["mean_factual"] == pytest.approx(good["factual"] / 2)  # dont_know counts 0
    assert _reason(client, mains[2])["status"] == "failed"
    assert body["knowledge"]["provisional"] is False  # the main round is complete
    assert body["provisional"] is False


def test_series_withheld_and_off(client, world):
    use(client)
    rid = start_round(client, world, "exam", 2, follow_ups=ON)["id"]
    post_answer(client, answer_current(client, rid))
    post_answer(client, answer_current(client, rid))
    body = get(client, f"/reports/rounds/{rid}")
    assert (body["withheld"], body["follow_ups"]) == (True, None)
    rid = start_round(client, world, "practice", 1)["id"]
    post_answer(client, answer_current(client, rid))
    act(client, rid, "next", "feedback")
    assert get(client, f"/reports/rounds/{rid}")["follow_ups"] is None


def test_exam_series_after_the_end(client, world):
    use(client, assessor=graded())
    rid, _, _ = main_and_follow_up(client, world, "exam")
    body = get(client, f"/reports/rounds/{rid}")
    assert body["status"] == "finished"
    assert (body["follow_ups"]["asked"], body["follow_ups"]["good_or_better"]) == (1, 1)


def test_history_stays_read_only_with_follow_ups(client, world, db):
    use(client)
    for _ in range(3):
        rid, _, _ = main_and_follow_up(client, world, count=2)
        act(client, rid, "next", "feedback")
        act(client, rid, "question_ready", "question")
        act(client, rid, "dont_know", "preparation")
        act(client, rid, "next", "feedback")
    seen = []

    def listener(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", listener)
    try:
        rows = get(client, "/reports/history")
    finally:
        event.remove(Engine, "before_cursor_execute", listener)
    assert len(rows) == 3
    assert len(seen) <= 60, len(seen)
    writes = [s for s in seen if s.split()[0].upper() in ("INSERT", "UPDATE", "DELETE")]
    assert writes == []
    db.expire_all()
    assert db.scalar(select(FollowUp.id)) is not None


def test_series_counts_follow_up_answers_still_processing(client, world):
    use(client, assessor=graded())
    rid = start_round(client, world, "exam", 1, follow_ups=ON)["id"]
    post_answer(client, answer_current(client, rid))
    follow = answer_current(client, rid)  # stopped; its audio is not stored yet
    body = get(client, f"/reports/rounds/{rid}")
    assert body["status"] == "finished"
    series = body["follow_ups"]
    assert (series["asked"], series["unresolved"], series["processing"]) == (1, 1, 1)
    assert series["provisional"] is True
    post_answer(client, follow)
    series = get(client, f"/reports/rounds/{rid}")["follow_ups"]
    assert (series["unresolved"], series["processing"], series["good_or_better"]) == (0, 0, 1)
