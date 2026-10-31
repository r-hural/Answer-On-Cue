"""The repeat series and the original/repeat links in the reports, never in the round score."""

import pytest
from test_adaptive_extension import finish
from test_adaptive_support import build_world
from test_attempts_support import act
from test_repetition_support import (
    answer_again,
    answer_ready,
    get,
    repeat_world,
    start_practice,
    use_scripted,
)
from test_reports_history import count_statements
from test_topics_support import upgrade

from app.db.session import SessionLocal


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    return repeat_world(db)


def repeated_round(client, world, first=35, again=70) -> tuple[int, int, int]:
    """Two questions: the first answered `first` and repeated `again`, the second Very good."""
    rid = start_practice(client, world, count=2)
    original = answer_ready(client, rid, factual=first)
    answer_again(client, rid, original)
    repeat = answer_ready(client, rid, factual=again)
    act(client, rid, "next", "feedback")
    answer_ready(client, rid)
    act(client, rid, "next", "feedback")
    return rid, original, repeat


def test_repeat_series(client, world):
    use_scripted(client)
    rid, original, repeat = repeated_round(client, world)
    report = get(client, f"/reports/rounds/{rid}")
    repeats = report["repeats"]
    assert (repeats["asked"], repeats["improved"], repeats["good_or_better"]) == (1, 1, 1)
    (row,) = repeats["rows"]
    assert row["ordinal"] == 1 and row["factual_change"] == pytest.approx(35)
    assert row["original"]["attempt_id"] == original and row["again"]["attempt_id"] == repeat
    assert row["below_good"] is False
    assert repeats["knowledge"]["mean_factual"] == pytest.approx(70)
    assert repeats["delivery"]["count_included"] == 1
    first, second = report["items"]
    assert first["again"]["band"] == "good" and first["again"]["attempt_id"] == repeat
    assert first["attempt_id"] == original and first["factual"] == pytest.approx(35)
    assert second["again"] is None
    assert report["knowledge"]["mean_factual"] == pytest.approx((35 + 88.75) / 2)

    plain = start_practice(client, world, count=1)
    answer_ready(client, plain)
    assert get(client, f"/reports/rounds/{plain}")["repeats"] is None


def test_skipped_and_dont_know_repeats(client, world):
    use_scripted(client)
    rid = start_practice(client, world, count=2)
    first = answer_ready(client, rid, factual=35)
    answer_again(client, rid, first)
    act(client, rid, "skip", "question")  # a skipped repeat is no answer: left out
    act(client, rid, "next", "feedback")
    second = answer_ready(client, rid, factual=35)
    answer_again(client, rid, second)
    act(client, rid, "question_ready", "question")
    act(client, rid, "dont_know", "preparation")  # "I don't know" counts 0
    report = get(client, f"/reports/rounds/{rid}")
    repeats = report["repeats"]
    assert repeats["asked"] == 1
    (row,) = repeats["rows"]
    assert row["ordinal"] == 2 and row["again"]["outcome"] == "dont_know"
    assert row["below_good"] is True
    assert repeats["knowledge"]["primary_score"] == 0
    assert report["items"][0]["again"] is None


def test_attempt_report_links(client, world):
    use_scripted(client)
    rid, original, repeat = repeated_round(client, world, first=35, again=70)
    body = get(client, f"/reports/attempts/{repeat}")
    assert body["attempt_kind"] == "immediate"
    assert body["again_attempt_id"] is None
    first = get(client, f"/reports/attempts/{original}")
    assert first["attempt_kind"] == "initial"
    assert first["again_attempt_id"] == repeat
    assert first["original"] is None
    brief = body["original"]
    assert brief["attempt_id"] == original
    assert brief["band"] == "needs_improvement"
    assert brief["factual"] == pytest.approx(35)
    assert brief["total"] == pytest.approx(first["result"]["assessment"]["scores"]["total"])
    metrics = first["result"]["metrics"]
    assert brief["preparation_used_ms"] == metrics["preparation_used_ms"]
    assert brief["long_pause_count"] == metrics["long_pause_count"]
    assert brief["fluency"] is None  # ai_for(): no fluency judgement


def test_repeat_report_has_no_follow_up(client, world):
    use_scripted(client)
    rid = start_practice(client, world, count=1, follow_ups={"enabled": True})
    original = answer_ready(client, rid, factual=35)
    answer_ready(client, rid)  # the follow-up
    answer_again(client, rid, original)
    repeat = answer_ready(client, rid, factual=70)
    assert get(client, f"/reports/attempts/{original}")["follow_up"]["asked"] is True
    assert get(client, f"/reports/attempts/{repeat}")["follow_up"] is None  # no follow-up


def test_extension_round_keeps_its_repeats(client, db):
    world = build_world(db, name="Rozszerzenie", per_objective=(2, 2, 2))
    use_scripted(client)
    rid = start_practice(client, world, count=3, extension={"enabled": True, "question_count": 1})
    finish(db, rid, [{"band": "weak"}] * 3)
    started = client.post(f"/api/rounds/{rid}/extension")
    assert started.status_code == 201, started.text
    child = started.json()
    assert child["config"]["immediate_repeat"] is True  # model_copy keeps it
    original = answer_ready(client, child["id"], factual=35)
    answer_again(client, child["id"], original)
    answer_ready(client, child["id"], factual=70)
    report = get(client, f"/reports/rounds/{child['id']}")
    assert report["repeats"]["asked"] == 1
    assert report["knowledge"]["mean_factual"] == pytest.approx(35)


def test_history_ignores_repeats(client, world, db):
    use_scripted(client)
    plain = start_practice(client, world, count=3)
    repeated = start_practice(client, world, count=3)
    for round_id, repeat in ((plain, False), (repeated, True)):
        for _ in range(3):
            original = answer_ready(client, round_id, factual=35)
            if repeat:
                answer_again(client, round_id, original)
                answer_ready(client, round_id, factual=88.75)
            act(client, round_id, "next", "feedback")
    rows = {r["round_id"]: r for r in get(client, "/reports/history")}
    for key in ("primary_score", "band_distribution", "median_preparation_used_ms", "asked"):
        assert rows[repeated][key] == rows[plain][key]
    assert rows[repeated]["band_distribution"]["needs_improvement"] == 3
    assert rows[repeated]["primary_score"] == pytest.approx(0.8 * 35 + 18)

    # the same round shape in a second subject, without repeats: equal statement counts
    other = repeat_world(db, name="Geografia")
    for _ in range(2):
        alone = start_practice(client, other, count=3)
        for _ in range(3):
            answer_ready(client, alone, factual=35)
            act(client, alone, "next", "feedback")

    def counted(subject_id: int) -> int:
        return count_statements(
            client, lambda: get(client, f"/reports/history?subject_id={subject_id}")
        )

    assert counted(world.subject.id) == counted(other.subject.id)  # 3 repeats vs none
