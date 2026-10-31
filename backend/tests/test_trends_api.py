import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from test_coverage_support import make_answer, make_objective_question, make_round
from test_topics_support import make_subject, upgrade
from test_trends_support import add_attempt

from app.db.session import SessionLocal
from app.features.attempts.models import AttemptProcessing
from app.features.rounds.models import Attempt, RoundItem

ANCHOR = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def day(n: int) -> datetime:
    return ANCHOR - timedelta(days=n)


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def subject(db):
    row = make_subject(db, "Biologia")
    db.commit()
    return row


def question(
    db, subject, text="Pytanie?", *, style="short", difficulty="recall", policy="materials"
):
    version = make_objective_question(
        db, subject.id, None, None, text, source_policy=policy, difficulty=difficulty
    )
    version.style = style
    db.commit()
    return version


def expanded(db, round_):
    config = json.loads(round_.config_json)
    config["source_policy"] = "expanded"
    round_.config_json = json.dumps(config)
    db.commit()


def get(client, subject, **params):
    return client.get("/api/trends", params={"subject_id": subject.id, **params})


def test_trends_endpoint_shape(client, db, subject, settings):
    q = question(db, subject)
    for n in (3, 2, 1):
        make_answer(db, make_round(db, subject.id, at=day(n)), q, at=day(n), band="good")
    response = get(client, subject)
    assert response.status_code == 200
    body = response.json()
    assert [p["started_at"][:10] for p in body["points"]] == [
        day(n).date().isoformat() for n in (3, 2, 1)
    ]
    assert all(p["started_at"].endswith(("Z", "+00:00")) for p in body["points"])
    assert body["band_bounds"] == list(settings.factual_band_bounds)
    assert body["subject_name"] == "Biologia"
    assert body["filters"]["series"] == "main"
    missing = client.get("/api/trends", params={"subject_id": 9999})
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Subject not found."
    assert get(client, subject, series="bogus").status_code == 422
    assert client.get("/api/trends").status_code == 422


def test_source_policy_groups_are_separate(client, db, subject):
    strict = question(db, subject, "Własne?")
    wide = question(db, subject, "Szersze?", policy="expanded")
    for n in (3, 2):
        round_ = make_round(db, subject.id, at=day(n))
        make_answer(db, round_, strict, at=day(n), factual=40.0, band="needs_improvement")
    round_ = make_round(db, subject.id, at=day(1))
    expanded(db, round_)
    make_answer(db, round_, wide, at=day(1), factual=90.0, band="very_good")
    points = get(client, subject).json()["points"]
    assert [p["factual"] for p in points] == [40.0, 40.0]
    points = get(client, subject, source_policy="expanded").json()["points"]
    assert [p["factual"] for p in points] == [90.0]


def test_filters_style_difficulty(client, db, subject):
    short = question(db, subject, "Krótkie?", style="short", difficulty="recall")
    deep = question(db, subject, "Głębokie?", style="open", difficulty="analysis")
    round_ = make_round(db, subject.id, at=day(1))
    make_answer(db, round_, short, at=day(1), factual=30.0, band="needs_improvement")
    make_answer(db, round_, deep, at=day(1), factual=90.0, band="very_good")
    assert get(client, subject).json()["points"][0]["factual"] == 60.0
    assert get(client, subject, style="short").json()["points"][0]["factual"] == 30.0
    assert get(client, subject, difficulty="analysis").json()["points"][0]["factual"] == 90.0
    assert get(client, subject, style="short", difficulty="analysis").json()["points"] == []


def test_extension_round_hidden_from_main(client, db, subject):
    q = question(db, subject)
    parent = make_round(db, subject.id, at=day(2))
    make_answer(db, parent, q, at=day(2), band="good")
    child = make_round(db, subject.id, at=day(1))
    child.round_type = "extension"
    child.parent_round_id = parent.id
    db.commit()
    make_answer(db, child, q, at=day(1), band="good")
    assert [p["round_id"] for p in get(client, subject).json()["points"]] == [parent.id]
    ext = get(client, subject, series="extension").json()["points"]
    assert [p["round_id"] for p in ext] == [child.id]


def test_limit_newest_fifty(client, db, subject):
    q = question(db, subject)
    ids = []
    for n in range(52):
        at = ANCHOR - timedelta(hours=52 - n)
        round_ = make_round(db, subject.id, at=at)
        make_answer(db, round_, q, at=at, band="good")
        ids.append(round_.id)
    points = get(client, subject).json()["points"]
    assert [p["round_id"] for p in points] == ids[2:]


class Statements:
    def __init__(self):
        self.reads = 0
        self.writes = 0

    def __call__(self, conn, cursor, statement, parameters, context, executemany):
        self.reads += 1
        if statement.lstrip().split(None, 1)[0].upper() in ("INSERT", "UPDATE", "DELETE"):
            self.writes += 1


def measure(client, subject) -> Statements:
    seen = Statements()
    event.listen(Engine, "before_cursor_execute", seen)
    try:
        assert get(client, subject).status_code == 200
    finally:
        event.remove(Engine, "before_cursor_execute", seen)
    return seen


def test_trends_query_budget_and_read_only(client, db, subject):
    q = question(db, subject)

    def add_round(n, answers):
        at = ANCHOR - timedelta(hours=100 - n)
        round_ = make_round(db, subject.id, at=at)
        for _ in range(answers):
            make_answer(db, round_, q, at=at, band="good")
        return round_

    for n in range(2):
        add_round(n, 1)
    overdue_round = add_round(50, 1)
    make_answer(
        db,
        overdue_round,
        q,
        at=datetime.now(UTC) - timedelta(minutes=10),
        processing="processing",
    )
    small = measure(client, subject)
    for n in range(2, 8):
        add_round(n, 1)
    more_rounds = measure(client, subject)
    for n in range(10, 16):
        add_round(n, 6)
    more_answers = measure(client, subject)

    assert small.reads == more_rounds.reads == more_answers.reads
    assert small.reads <= 4
    assert (small.writes, more_rounds.writes, more_answers.writes) == (0, 0, 0)

    db.expire_all()
    waiting = db.query(Attempt).filter(Attempt.processing_status == "processing").one()
    assert db.query(AttemptProcessing).filter_by(attempt_id=waiting.id).count() == 0
    assert waiting.outcome == "answered"
    points = get(client, subject).json()["points"]
    point = next(p for p in points if p["round_id"] == overdue_round.id)
    assert (point["unresolved"], point["provisional"], point["answers"]) == (1, True, 1)


def test_second_attempt_stays_out_unless_asked(client, db, subject):
    q = question(db, subject)
    round_ = make_round(db, subject.id, at=day(1))
    first = make_answer(db, round_, q, at=day(1), factual=40.0, band="needs_improvement")
    add_attempt(db, first.round_item_id, "immediate", at=day(1), factual=90.0)
    assert db.query(RoundItem).count() == 1
    assert get(client, subject).json()["points"][0]["factual"] == 40.0
    assert get(client, subject, include_assisted=True).json()["points"][0]["factual"] == 65.0


def test_comparison_group_is_the_round_config_policy(client, db, subject):
    # Synthetic data: normally a round's config policy always equals its questions' policy.
    # Here they differ on purpose, to pin that the round's RoundConfig.source_policy decides.
    q_materials = question(db, subject, "Własne?")
    q_expanded = question(db, subject, "Szersze?", policy="expanded")
    wide = make_round(db, subject.id, at=day(2))
    expanded(db, wide)
    make_answer(db, wide, q_materials, at=day(2), factual=90.0, band="very_good")
    strict = make_round(db, subject.id, at=day(1))
    make_answer(db, strict, q_expanded, at=day(1), factual=40.0, band="needs_improvement")
    assert [p["round_id"] for p in get(client, subject).json()["points"]] == [strict.id]
    points = get(client, subject, source_policy="expanded").json()["points"]
    assert [p["round_id"] for p in points] == [wide.id]


def test_answer_again_in_extension_round_is_only_immediate(client, db, subject):
    q = question(db, subject)
    parent = make_round(db, subject.id, at=day(2))
    make_answer(db, parent, q, at=day(2), factual=40.0, band="needs_improvement")
    child = make_round(db, subject.id, at=day(1))
    child.round_type = "extension"
    child.parent_round_id = parent.id
    db.commit()
    first = make_answer(db, child, q, at=day(1), factual=50.0, band="needs_improvement")
    add_attempt(db, first.round_item_id, "immediate", at=day(1), factual=90.0)

    def factuals(**params):
        points = get(client, subject, **params).json()["points"]
        return {p["round_id"]: p["factual"] for p in points}

    assert factuals() == {parent.id: 40.0}
    assert factuals(include_assisted=True) == {parent.id: 40.0}
    assert factuals(series="extension") == {child.id: 50.0}
    assert factuals(series="extension", include_assisted=True) == {child.id: 50.0}
    assert factuals(series="immediate") == {child.id: 90.0}
