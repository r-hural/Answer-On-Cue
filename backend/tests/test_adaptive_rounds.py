from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from test_adaptive_support import adaptive_world, build_world
from test_coverage_support import make_answer, make_objective_question, make_round
from test_reports_history import count_statements
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.rounds import service
from app.features.rounds.models import Attempt, Round, RoundItem

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
CATEGORY_OF = {0: "weak", 1: "review", 2: "unpractised"}


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def create(client, subject_id: int, **config):
    config.setdefault("scope", {"kind": "subject"})
    return client.post("/api/rounds", json={"subject_id": subject_id, "config": config})


def start(client, subject_id: int, **config) -> dict:
    response = create(client, subject_id, **config)
    assert response.status_code == 201, response.text
    return response.json()


def selections(db, round_id: int) -> dict[int, str | None]:
    return {
        item.question_version_id: item.selection
        for item in db.query(RoundItem).filter_by(round_id=round_id, role="main")
    }


def test_create_adaptive_round(client, db):
    world = adaptive_world(db, NOW)
    state = start(client, world.subject.id, round_type="adaptive", question_count=4)
    assert state["config"]["round_type"] == "adaptive"
    assert db.get(Round, state["id"]).round_type == "adaptive"
    chosen = selections(db, state["id"])
    assert Counter(chosen.values()) == {"weak": 2, "unpractised": 1, "review": 1}
    for version_id, selection in chosen.items():
        assert selection == CATEGORY_OF[world.objective_of(version_id)]
    assert state["current_attempt"]["attempt_kind"] == "initial"
    assert db.query(Attempt).join(RoundItem).filter(RoundItem.round_id == state["id"]).count() == 1


def test_standard_round_unchanged(client, db, monkeypatch):
    world = make_world(db)
    ids = [q.id for q in world["questions"]]
    monkeypatch.setattr(service.random, "randrange", lambda _: 424242)
    state = start(client, world["subject"].id, question_count=5)
    # Fixed before the create_round refactor: the same seed chooses the same questions.
    assert [ids.index(i["question_version_id"]) for i in state["items"]] == [1, 7, 0, 6, 2]
    assert set(selections(db, state["id"]).values()) == {None}


def test_new_subject_adaptive_is_balanced(client, db):
    world = build_world(db)
    state = start(client, world.subject.id, round_type="adaptive", question_count=6)
    assert set(selections(db, state["id"]).values()) == {"balanced"}
    # Balanced over the topics, like a standard round.
    assert Counter(world.objective_of(i) for i in selections(db, state["id"])) == {0: 2, 1: 2, 2: 2}


def test_running_exam_evidence_ignored(client, db):
    world = adaptive_world(db, NOW)
    running = make_round(db, world.subject.id, at=NOW, timing="exam", status="active")
    make_answer(db, running, world.questions[2][0], at=NOW, band="weak")
    state = start(client, world.subject.id, round_type="adaptive", question_count=4)
    for version_id, selection in selections(db, state["id"]).items():
        assert selection == CATEGORY_OF[world.objective_of(version_id)]


def test_adaptive_too_few_is_409(client, db):
    world = adaptive_world(db, NOW)
    for round_type in ("standard", "adaptive"):
        response = create(client, world.subject.id, round_type=round_type, question_count=13)
        assert response.status_code == 409
        assert response.json()["detail"] == {
            "message": "Not enough questions for the selected settings: 12 available.",
            "available": 12,
        }


def test_adaptive_create_query_budget(client, db):
    world = adaptive_world(db, NOW)

    def call():
        start(client, world.subject.id, round_type="adaptive", question_count=4)

    before = count_statements(client, call)
    for n in range(5):
        at = NOW - timedelta(days=5 + n)
        past = make_round(db, world.subject.id, at=at)
        for objective in (0, 1, 2):
            make_answer(db, past, world.questions[objective][1 + n % 3], at=at, band="good")
    assert count_statements(client, call) == before


def test_round_report_selection_counts(client, db):
    world = adaptive_world(db, NOW)
    adaptive = start(client, world.subject.id, round_type="adaptive", question_count=4)
    report = client.get(f"/api/reports/rounds/{adaptive['id']}").json()
    assert report["selection_counts"] == {"weak": 2, "unpractised": 1, "review": 1}
    standard = start(client, world.subject.id, question_count=2)
    assert client.get(f"/api/reports/rounds/{standard['id']}").json()["selection_counts"] is None
    rows = client.get("/api/reports/history", params={"subject_id": world.subject.id}).json()
    types = {row["round_id"]: row["round_type"] for row in rows}
    assert types[adaptive["id"]] == "adaptive"
    assert types[standard["id"]] == "standard"


def test_adaptive_expanded_round_uses_expanded_layer_and_pool(client, db):
    """An expanded adaptive round takes its categories from the expanded layer
    and its questions from the expanded pool only."""
    world = adaptive_world(db, NOW)  # materials: O1 Weak, O2 Strong, O3 Not practised
    expanded: dict[int, list] = {}
    for n, (topic, objective) in enumerate(zip(world.topics, world.objectives, strict=True)):
        expanded[n] = []
        for k in range(4):
            question = make_objective_question(
                db,
                world.subject.id,
                topic.id,
                objective.id,
                f"Rozszerzone {k + 1} o O{n + 1}?",
                source_policy="expanded",
            )
            question.style = "short"
            expanded[n].append(question)
    db.commit()
    # Expanded layer, the other way round: O1 Strong, O2 Weak, O3 Not practised.
    for days in (3, 2):
        at = NOW - timedelta(days=days)
        make_answer(
            db, make_round(db, world.subject.id, at=at), expanded[0][0], at=at, band="very_good"
        )
    at = NOW - timedelta(days=1)
    make_answer(db, make_round(db, world.subject.id, at=at), expanded[1][0], at=at, band="weak")
    objective_of = {q.id: n for n, items in expanded.items() for q in items}

    state = start(
        client, world.subject.id, round_type="adaptive", source_policy="expanded", question_count=4
    )
    chosen = selections(db, state["id"])
    assert set(chosen) <= set(objective_of)  # the expanded pool only
    assert Counter(chosen.values()) == {"weak": 2, "unpractised": 1, "review": 1}
    expanded_category = {0: "review", 1: "weak", 2: "unpractised"}
    for version_id, selection in chosen.items():
        assert selection == expanded_category[objective_of[version_id]]
