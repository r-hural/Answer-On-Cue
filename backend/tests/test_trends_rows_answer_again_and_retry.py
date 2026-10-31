"""Trends over the rows that the real answer-again and retry-round flows write.

Answer again writes an `immediate` attempt on the main item; the retry round writes `retry`
attempts in a `round_type="retry"` round. Neither may move the original round's `main` point,
and each shows up in its own series.
"""

import pytest
from test_attempts_support import act
from test_repetition_support import (
    answer_again,
    answer_ready,
    repeat_world,
    start_practice,
    use_scripted,
)
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.trends import service
from app.features.trends.schemas import TrendFilters

LOW = 35.0  # needs_improvement under (30, 60, 80): offered for retry
AGAIN = 88.75
RETRIED = (88.75, 70.0)
# Everything about the point except which round it is and when it started.
VOLATILE = ("round_id", "started_at", "changes")


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def trends(client, subject_id: int, **params) -> list[dict]:
    response = client.get("/api/trends", params={"subject_id": subject_id, **params})
    assert response.status_code == 200, response.text
    return response.json()["points"]


def by_round(points: list[dict]) -> dict[int, dict]:
    return {p["round_id"]: {k: v for k, v in p.items() if k not in VOLATILE} for p in points}


def two_question_round(client, world, *, repeat: bool) -> int:
    """Both questions answered LOW; with `repeat`, the first is answered again AGAIN."""
    rid = start_practice(client, world, count=2, retry_round=True)
    original = answer_ready(client, rid, factual=LOW)
    if repeat:
        answer_again(client, rid, original)
        answer_ready(client, rid, factual=AGAIN)
    act(client, rid, "next", "feedback")
    answer_ready(client, rid, factual=LOW)
    final = act(client, rid, "next", "feedback")
    assert final["status"] == "finished", final
    return rid


def test_main_series_ignores_answer_again_and_retry_rounds(client, db, settings):
    use_scripted(client)
    world = repeat_world(db)
    subject_id = world.subject.id

    plain = two_question_round(client, world, repeat=False)
    repeated = two_question_round(client, world, repeat=True)
    before_retry = by_round(trends(client, subject_id))
    assert set(before_retry) == {plain, repeated}
    # Answer again did not move the original round's main point: it equals the plain round's.
    assert before_retry[repeated] == before_retry[plain]
    assert before_retry[repeated]["factual"] == pytest.approx(LOW)
    assert before_retry[repeated]["assisted"] == 0

    # The retry round, through its real endpoint and flow: both originals were below Good.
    started = client.post(f"/api/rounds/{repeated}/retry")
    assert started.status_code == 201, started.text
    retry_id = started.json()["id"]
    for factual in RETRIED:
        answer_ready(client, retry_id, factual=factual)
        state = act(client, retry_id, "next", "feedback")
    assert state["status"] == "finished", state

    main = trends(client, subject_id)
    assert [p["round_id"] for p in main] == [plain, repeated]  # no retry point in main
    assert by_round(main) == before_retry  # unchanged by the retry round

    # The service gives the same main series as the endpoint.
    direct = service.trends(db, settings, TrendFilters(subject_id=subject_id))
    assert [p.model_dump(mode="json") for p in direct.points] == main

    (immediate,) = trends(client, subject_id, series="immediate")
    assert immediate["round_id"] == repeated
    assert immediate["answers"] == 1
    assert immediate["factual"] == pytest.approx(AGAIN)

    (retry,) = trends(client, subject_id, series="retry")
    assert retry["round_id"] == retry_id
    assert retry["round_type"] == "retry"
    assert retry["answers"] == len(RETRIED)
    assert retry["factual"] == pytest.approx(sum(RETRIED) / len(RETRIED))

    # "Include answers with help" adds the answer-again attempt to its own round's main point.
    helped = by_round(trends(client, subject_id, include_assisted="true"))
    assert set(helped) == {plain, repeated}
    assert helped[plain] == before_retry[plain]
    assert helped[repeated]["assisted"] == 1
    assert helped[repeated]["answers"] == 3
    assert helped[repeated]["factual"] == pytest.approx((LOW + LOW + AGAIN) / 3)
