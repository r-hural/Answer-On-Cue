"""The end-of-round retry round: selection, start, its own score and the reports."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from test_adaptive_extension import finish, items_of
from test_adaptive_support import answer_item, build_world
from test_attempts_support import act
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.questions.models import QuestionVersion
from app.features.rounds import retry
from app.features.rounds.models import Attempt, Round, RoundItem
from app.features.rounds.schemas import RETRY_NOT_NOW

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
WEAK = {"band": "weak"}
LOW = {"band": "needs_improvement"}
GOOD = {"band": "good"}
VERY_GOOD = {"band": "very_good"}


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    return build_world(db, per_objective=(3, 3, 3))


def start_round(client, world, count, *, timing="practice", retry_on=True, **config) -> int:
    config.setdefault("scope", {"kind": "subject"})
    config.setdefault("style", "short")
    body = {
        "subject_id": world.subject.id,
        "config": {
            "question_count": count,
            "feedback_timing": timing,
            "retry_round": retry_on,
            **config,
        },
    }
    response = client.post("/api/rounds", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def get_offer(client, round_id: int) -> dict:
    response = client.get(f"/api/rounds/{round_id}/retry")
    assert response.status_code == 200, response.text
    return response.json()


def get(client, path: str):
    response = client.get(f"/api{path}")
    assert response.status_code == 200, response.text
    return response.json()


def start_retry(client, round_id: int, expected: int = 201) -> dict:
    response = client.post(f"/api/rounds/{round_id}/retry")
    assert response.status_code == expected, response.text
    return response.json()


def answer_retry(db, child_id: int, answers: list[dict], status: str = "finished") -> None:
    """Answers the retry round's items in order with retry attempts, then closes it."""
    at = NOW + timedelta(hours=1)
    for item in items_of(db, child_id):
        if db.query(Attempt).filter_by(round_item_id=item.id).count() == 0:
            db.add(Attempt(round_item_id=item.id, attempt_kind="retry", outcome="pending"))
            db.commit()
    finish(db, child_id, [], status=status)
    for item, answer in zip(items_of(db, child_id), answers, strict=False):
        answer_item(db, item, at=at, **answer)


def weak_round(client, db, world, answers, **config) -> int:
    round_id = start_round(client, world, len(answers), **config)
    finish(db, round_id, answers)
    return round_id


def test_offer_statuses(client, db, world):
    off = start_round(client, world, 2, retry_on=False)
    finish(db, off, [WEAK, WEAK], status="abandoned")
    assert get_offer(client, off)["status"] == "off"  # off comes before ended_early

    active = start_round(client, world, 2)
    assert get_offer(client, active)["status"] == "not_finished"
    db.get(Round, active).status = "paused"
    db.commit()
    assert get_offer(client, active)["status"] == "not_finished"

    ended = start_round(client, world, 2)
    finish(db, ended, [WEAK], status="abandoned")
    assert get_offer(client, ended)["status"] == "ended_early"

    good = weak_round(client, db, world, [GOOD, VERY_GOOD])
    offer = get_offer(client, good)
    assert (offer["status"], offer["count"], offer["ordinals"]) == ("none_below", 0, [])

    real_now = datetime.now(UTC)
    waiting = weak_round(client, db, world, [WEAK, {"processing": "processing"}])
    last = items_of(db, waiting)[-1]
    attempt = db.query(Attempt).filter_by(round_item_id=last.id).one()
    attempt.ended_at = real_now  # just stopped: its audio may still arrive
    db.commit()
    assert get_offer(client, waiting)["status"] == "waiting"
    answer_item(db, last, at=NOW, band="good")
    offer = get_offer(client, waiting)
    assert (offer["status"], offer["count"], offer["ordinals"]) == ("ready", 1, [1])

    overdue = weak_round(client, db, world, [WEAK, {"processing": "processing"}])
    last = items_of(db, overdue)[-1]
    attempt = db.query(Attempt).filter_by(round_item_id=last.id).one()
    attempt.ended_at = real_now - timedelta(minutes=10)
    db.commit()
    offer = get_offer(client, overdue)
    assert (offer["status"], offer["unresolved"]) == ("ready", 1)
    db.expire_all()
    assert db.get(Attempt, attempt.id).processing_status == "processing"  # the GET did not settle

    with_extension = start_round(client, world, 2, extension={"enabled": True, "question_count": 1})
    finish(db, with_extension, [WEAK, WEAK])
    extension = client.post(f"/api/rounds/{with_extension}/extension")
    assert extension.status_code == 201, extension.text
    assert get_offer(client, with_extension)["status"] == "extension_open"
    child = db.get(Round, extension.json()["id"])
    child.status = "finished"
    db.commit()
    assert get_offer(client, with_extension)["status"] == "ready"

    started = start_retry(client, overdue)
    offer = get_offer(client, overdue)
    assert offer["status"] == "started"
    assert (offer["retry_round_id"], offer["retry_status"]) == (started["id"], "active")


def test_retry_selection_from_stored_band(client, db, world):
    round_id = weak_round(
        client,
        db,
        world,
        [
            {"band": "weak", "factual": 25.0},
            {"band": "needs_improvement", "factual": 55.0},
            {"band": "good", "factual": 70.0},
            {"outcome": "dont_know"},
            {"outcome": "skipped"},
            {"band": None, "assessment_status": "no_speech"},
            {"outcome": "technical_failure"},
        ],
    )
    offer = get_offer(client, round_id)
    assert offer["status"] == "ready"
    assert offer["ordinals"] == [1, 2, 4, 5] and offer["count"] == 4
    assert offer["unresolved"] == 2  # no speech and the technical failure

    calm = weak_round(client, db, world, [{"band": "good", "factual": 55.0}, GOOD])
    assert get_offer(client, calm)["status"] == "none_below"  # the stored band decides


def test_retry_selection_uses_original_not_repeat(client, db, world):
    round_id = start_round(client, world, 2, immediate_repeat=True)
    finish(db, round_id, [WEAK, GOOD])
    first = items_of(db, round_id)[0]
    original = db.query(Attempt).filter_by(round_item_id=first.id).one()
    db.add(
        Attempt(
            round_item_id=first.id,
            attempt_kind="immediate",
            parent_attempt_id=original.id,
            outcome="pending",
        )
    )
    db.commit()
    answer_item(db, first, at=NOW, band="very_good")  # the repeat
    assert get_offer(client, round_id)["ordinals"] == [1]
    child = start_retry(client, round_id)
    item = db.get(RoundItem, child["items"][0]["id"])
    assert item.parent_item_id == first.id


def test_start_retry(client, db, world):
    round_id = weak_round(client, db, world, [WEAK, GOOD, LOW, {"outcome": "dont_know"}])
    parent_items = items_of(db, round_id)
    picked = [parent_items[0], parent_items[2], parent_items[3]]
    edited = db.get(QuestionVersion, picked[1].question_version_id)
    edited.status = "retired"  # the bank moved on: the retry still asks the asked version
    db.commit()

    child = start_retry(client, round_id)
    row = db.get(Round, child["id"])
    assert (row.round_type, row.parent_round_id) == ("retry", round_id)
    parent_config = get(client, f"/rounds/{round_id}")["config"]
    config = child["config"]
    assert config["round_type"] == "retry" and config["question_count"] == 3
    assert config["follow_ups"]["enabled"] is False
    assert config["extension"]["enabled"] is False
    assert (config["retry_round"], config["immediate_repeat"]) == (False, False)
    for key in (
        "scope",
        "source_policy",
        "feedback_timing",
        "prep_seconds",
        "answer_seconds",
        "question_audio",
        "band_bounds",
        "factual_weights",
    ):
        assert config[key] == parent_config[key], key
    items = items_of(db, child["id"])
    assert [i.parent_item_id for i in items] == [i.id for i in picked]
    assert [i.question_version_id for i in items] == [i.question_version_id for i in picked]
    assert [i.rubric_version_id for i in items] == [i.rubric_version_id for i in picked]
    assert [i.ordinal for i in items] == [0, 1, 2]

    for _ in range(3):
        act(client, child["id"], "question_ready", "question")
        act(client, child["id"], "dont_know", "preparation")
        act(client, child["id"], "next", "feedback")
    db.expire_all()
    kinds = {
        a.attempt_kind
        for a in db.scalars(select(Attempt).where(Attempt.round_item_id.in_([i.id for i in items])))
    }
    assert kinds == {"retry"}
    assert db.get(Round, child["id"]).status == "finished"
    assert get_offer(client, round_id)["status"] == "started"


def test_start_retry_twice_returns_same_round(client, db, world, monkeypatch):
    round_id = weak_round(client, db, world, [WEAK, GOOD])
    first = start_retry(client, round_id)
    again = start_retry(client, round_id, expected=200)
    assert again["id"] == first["id"]
    # a parallel start that read "ready" before the first one committed
    monkeypatch.setattr(retry, "_children", lambda session, round_id: {})
    raced = start_retry(client, round_id, expected=200)
    assert raced["id"] == first["id"]
    db.expire_all()
    assert db.query(Round).filter_by(parent_round_id=round_id, round_type="retry").count() == 1


def test_start_retry_not_ready_is_409(client, db, world):
    off = weak_round(client, db, world, [WEAK], retry_on=False)
    ended = start_round(client, world, 2)
    finish(db, ended, [WEAK], status="abandoned")
    active = start_round(client, world, 1)
    waiting = weak_round(client, db, world, [WEAK, {"processing": "processing"}])
    last = items_of(db, waiting)[-1]
    db.query(Attempt).filter_by(round_item_id=last.id).one().ended_at = datetime.now(UTC)
    db.commit()
    good = weak_round(client, db, world, [GOOD])
    opened = start_round(client, world, 1, extension={"enabled": True, "question_count": 1})
    finish(db, opened, [WEAK])
    assert client.post(f"/api/rounds/{opened}/extension").status_code == 201
    statuses = {}
    for round_id in (off, ended, active, waiting, good, opened):
        statuses[get_offer(client, round_id)["status"]] = round_id
        response = client.post(f"/api/rounds/{round_id}/retry")
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == RETRY_NOT_NOW
    assert set(statuses) == {
        "off",
        "ended_early",
        "not_finished",
        "waiting",
        "none_below",
        "extension_open",
    }
    assert db.query(Round).filter_by(round_type="retry").count() == 0


def test_retry_round_offers_no_retry(client, db, world):
    round_id = weak_round(
        client, db, world, [WEAK, WEAK], extension={"enabled": True, "question_count": 1}
    )
    child = start_retry(client, round_id)
    assert get_offer(client, child["id"])["status"] == "off"
    extension = client.post(f"/api/rounds/{round_id}/extension").json()
    assert extension["config"]["retry_round"] is True  # model_copy keeps it
    assert get_offer(client, extension["id"])["status"] == "off"


def _parent_view(report: dict) -> dict:
    return {k: report[k] for k in ("knowledge", "delivery", "items", "provisional")}


def test_retry_round_never_changes_parent(client, db, world):
    round_id = weak_round(client, db, world, [WEAK, LOW, GOOD])
    before = _parent_view(get(client, f"/reports/rounds/{round_id}"))
    (history_before,) = get(client, "/reports/history")
    child = start_retry(client, round_id)
    answer_retry(db, child["id"], [VERY_GOOD, VERY_GOOD])
    after = get(client, f"/reports/rounds/{round_id}")
    assert _parent_view(after) == before
    rows = {r["round_id"]: r for r in get(client, "/reports/history")}
    assert rows[round_id] == history_before
    assert after["retry"]["good_or_better"] == 2


def test_retry_round_own_score_counts_retry_attempts(client, db, world):
    round_id = weak_round(client, db, world, [WEAK, LOW, GOOD])
    child = start_retry(client, round_id)
    answer_retry(db, child["id"], [VERY_GOOD, WEAK])
    report = get(client, f"/reports/rounds/{child['id']}")
    knowledge = report["knowledge"]
    assert knowledge["primary_score"] == pytest.approx((88.0 + 20.0) / 2)
    assert knowledge["band_distribution"] == {
        "weak": 1,
        "needs_improvement": 0,
        "good": 0,
        "very_good": 1,
    }
    assert report["delivery"]["count_included"] == 2
    assert report["delivery"]["count_excluded_other_kind"] == 0


def test_retry_series_and_comparison(client, db, world):
    round_id = weak_round(client, db, world, [WEAK, GOOD, LOW, LOW])
    parent = get(client, f"/reports/rounds/{round_id}")
    assert parent["retry"] is None
    child = start_retry(client, round_id)
    answer_retry(db, child["id"], [VERY_GOOD, LOW, {"outcome": "dont_know"}])

    series = get(client, f"/reports/rounds/{round_id}")["retry"]
    assert series["round_id"] == child["id"] and series["status"] == "finished"
    assert (series["asked"], series["total"]) == (3, 3)
    assert (series["good_or_better"], series["still_below"]) == (1, 2)
    assert series["withheld"] is False
    assert series["knowledge"]["primary_score"] == pytest.approx((88 + 45 + 0) / 3)

    report = get(client, f"/reports/rounds/{child['id']}")
    assert report["parent_round_id"] == round_id
    comparison = report["retry_comparison"]
    assert comparison["parent_round_id"] == round_id
    assert comparison["parent_started_at"] == parent["started_at"]
    parent_items = [parent["items"][i] for i in (0, 2, 3)]
    for row, original in zip(comparison["rows"], parent_items, strict=True):
        for key in ("attempt_id", "outcome", "status", "band", "factual", "total", "fluency"):
            assert row["original"][key] == original[key], key
    assert [r["ordinal"] for r in comparison["rows"]] == [1, 2, 3]
    assert comparison["still_below"] == [2, 3]
    paired = comparison["paired"]
    originals = [p["factual"] for p in parent_items][:paired]
    assert paired == 3  # "I don't know" scores 0 on the retry side
    assert comparison["original_mean_factual"] == pytest.approx(sum(originals) / 3)
    assert comparison["retry_mean_factual"] == pytest.approx((88 + 45 + 0) / 3)
    assert comparison["retry_mean_total"] - comparison["original_mean_total"] == pytest.approx(
        (88 + 45 + 0) / 3 - (20 + 45 + 45) / 3
    )
    prep = parent_items[0]["preparation_used_ms"]
    assert comparison["original_median_preparation_ms"] == prep
    assert comparison["rows"][0]["factual_change"] == pytest.approx(88 - 20)

    exam = weak_round(client, db, world, [WEAK], timing="exam")
    exam_child = start_retry(client, exam)
    held = get(client, f"/reports/rounds/{exam}")["retry"]
    assert held["round_id"] == exam_child["id"]
    assert (held["withheld"], held["knowledge"]) == (True, None)
    assert get(client, f"/reports/rounds/{exam_child['id']}")["retry_comparison"] is None


def test_retry_attempt_report_shows_original(client, db, world):
    round_id = weak_round(client, db, world, [WEAK, GOOD])
    child = start_retry(client, round_id)
    answer_retry(db, child["id"], [VERY_GOOD])
    attempt = db.query(Attempt).filter_by(round_item_id=child["items"][0]["id"]).one()
    body = get(client, f"/reports/attempts/{attempt.id}")
    assert body["attempt_kind"] == "retry" and body["round_type"] == "retry"
    original = items_of(db, round_id)[0]
    first = db.query(Attempt).filter_by(round_item_id=original.id).one()
    assert body["original"]["attempt_id"] == first.id
    assert body["original"]["band"] == "weak"


def test_retry_history_and_list(client, db, world):
    round_id = weak_round(client, db, world, [WEAK, GOOD])
    (before,) = get(client, "/reports/history")
    child = start_retry(client, round_id)
    answer_retry(db, child["id"], [GOOD])
    rows = get(client, "/reports/history")
    assert [r["round_id"] for r in rows] == [child["id"], round_id]
    retry_row, parent_row = rows
    assert retry_row["round_type"] == "retry"
    assert retry_row["parent_round_id"] == round_id
    assert retry_row["parent_started_at"] == parent_row["started_at"]
    assert retry_row["primary_score"] == pytest.approx(66.0)
    assert parent_row == before
    assert parent_row["parent_round_id"] is None
    listed = get(client, f"/rounds?subject_id={world.subject.id}")
    assert [r["id"] for r in listed] == [child["id"], round_id]
    assert listed[0]["round_type"] == "retry"


def test_retry_is_assisted_coverage_evidence(client, db, world):
    round_id = weak_round(client, db, world, [WEAK])
    child = start_retry(client, round_id)
    answer_retry(db, child["id"], [VERY_GOOD])
    body = get(client, f"/coverage/subjects/{world.subject.id}")
    lines = [line for topic in body["topics"] for line in topic["objectives"] if line["answers"]]
    (line,) = lines
    assert (line["status"], line["assisted"]) == ("improving", True)
    assert (line["answers"], line["independent_answers"]) == (2, 1)


def _statements(client, call) -> list[str]:
    seen: list[str] = []

    def listener(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", listener)
    try:
        call()
    finally:
        event.remove(Engine, "before_cursor_execute", listener)
    return seen


def _offer_statements(client, round_id: int) -> list[str]:
    return _statements(client, lambda: get_offer(client, round_id))


def test_retry_offer_budget_and_read_only(client, db):
    small_world = build_world(db, name="Mała", per_objective=(3, 3, 3))
    small = weak_round(client, db, small_world, [WEAK, GOOD])
    big_world = build_world(db, name="Duża", per_objective=(3, 3, 3))
    for _ in range(5):
        weak_round(client, db, big_world, [GOOD])
    big = weak_round(client, db, big_world, [WEAK, GOOD, LOW, GOOD, WEAK, GOOD, GOOD, LOW])
    small_seen = _offer_statements(client, small)
    big_seen = _offer_statements(client, big)
    assert get_offer(client, big)["count"] == 4
    assert len(small_seen) == len(big_seen), (small_seen, big_seen)
    writes = ("INSERT", "UPDATE", "DELETE")
    assert not [s for s in small_seen + big_seen if s.lstrip().upper().startswith(writes)]

    for status in ("started", "not_finished"):
        if status == "started":
            start_retry(client, small)
            start_retry(client, big)
        else:
            small = start_round(client, small_world, 2)
            big = start_round(client, big_world, 8)
        assert get_offer(client, small)["status"] == get_offer(client, big)["status"] == status
        assert len(_offer_statements(client, small)) == len(_offer_statements(client, big))
