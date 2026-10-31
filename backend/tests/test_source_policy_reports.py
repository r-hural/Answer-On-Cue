"""Source labels and conflicts in reports; History's source-policy filter."""

import json

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from test_attempts_support import (
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
from app.features.assessment.schemas import SourceConflict
from app.features.providers.fakes import FakeTextGenerator
from app.features.questions.models import QuestionVersion, RubricVersion
from app.features.reports.schemas import AttemptReport

ASSESS_TASK = "assessment.assess"
BEYOND = ["Szerszy kontekst spoza podręcznika."]


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    """make_world, with the Sparta questions turned into expanded ones (p2 beyond the materials)."""
    world = make_world(db)
    for q in world["questions"][4:]:
        q.source_policy = "expanded"
        row = db.scalar(select(RubricVersion).where(RubricVersion.question_version_id == q.id))
        rubric = json.loads(row.rubric_json)
        rubric["essential_points"][1]["basis"] = "expanded"
        rubric["essential_points"][1]["passage_ids"] = []
        rubric["beyond_material"] = BEYOND
        row.rubric_json = json.dumps(rubric)
    db.commit()
    return world


def use(client, statuses=None, conflict_on=None, **over):
    def assess(messages):
        result = ai_for(statuses=statuses, **over)
        if conflict_on is not None:
            ids = [int(i) for i in _passage_ids(messages)][:2]
            for j in result.points:
                j.disputed = j.point_id == conflict_on
            result.source_conflicts = [
                SourceConflict(point_id=conflict_on, passage_ids=ids, note="Źródła się różnią.")
            ]
        return result

    override_providers(client.app, FakeProviders(assessor=FakeTextGenerator({ASSESS_TASK: assess})))


def _passage_ids(messages) -> list[str]:
    import re

    return re.findall(r'<passage id="(\d+)"', messages[0].content)


def answered(client, world, policy, timing="practice", count=1):
    rid = start_round(client, world, timing, count, source_policy=policy)["id"]
    attempt = answer_current(client, rid)
    post_answer(client, attempt)
    return rid, attempt


def report(client, attempt_id) -> dict:
    response = client.get(f"/api/reports/attempts/{attempt_id}")
    assert response.status_code == 200, response.text
    return response.json()


def history(client, **params):
    return client.get("/api/reports/history", params=params)


def test_attempt_report_labels(client, world):
    use(client, statuses=["covered", "covered"])
    _, attempt = answered(client, world, "expanded")
    body = report(client, attempt)
    assert body["source_policy"] == "expanded"
    assert body["question_basis"] == "mixed"
    assert [(p["id"], p["basis"], p["disputed"]) for p in body["points"]] == [
        ("p1", "source", False),
        ("p2", "expanded", False),
    ]
    assert body["model_answer_display"] == "on_request"
    assert body["beyond_material"] == BEYOND
    assert body["source_conflicts"] == []


def test_beyond_material_withheld_with_the_model_answer(client, world):
    use(client, statuses=["covered", "covered"])
    _, attempt = answered(client, world, "expanded", timing="exam", count=2)
    body = report(client, attempt)
    assert body["model_answer_display"] is None
    assert body["beyond_material"] == []
    assert body["points"] == []


def test_attempt_report_conflicts(client, world):
    use(client, statuses=["covered", "missing"], conflict_on="p1")
    _, attempt = answered(client, world, "materials")
    body = report(client, attempt)
    assert [(p["id"], p["disputed"]) for p in body["points"]] == [("p1", True), ("p2", False)]
    (conflict,) = body["source_conflicts"]
    assert conflict["point_id"] == "p1"
    assert conflict["point_text"] == "Zgromadzenie ludowe"
    assert conflict["note"] == "Źródła się różnią."
    assert len(conflict["sources"]) == 2
    assert all(s["document_title"] == "Podręcznik" for s in conflict["sources"])
    assert all(len(s["excerpt"]) <= 240 for s in conflict["sources"])
    assert body["result"]["assessment"]["scores"]["completeness"] == 0.0  # p2 only, missing

    use(client, statuses=["covered", "missing"], conflict_on="p1")
    _, withheld = answered(client, world, "materials", timing="exam", count=2)
    body = report(client, withheld)
    assert body["source_conflicts"] == []


def test_v1_rows_report_unchanged(client, world):
    use(client)
    _, attempt = answered(client, world, "materials")
    body = report(client, attempt)
    assert body["source_policy"] == "materials"
    assert body["question_basis"] == "source"
    assert body["beyond_material"] == [] and body["source_conflicts"] == []
    assert all(p["basis"] == "source" and p["disputed"] is False for p in body["points"])
    new = {"source_policy", "question_basis", "beyond_material", "source_conflicts"}
    assert set(AttemptReport.model_fields) - new <= set(body)
    assert [(p["id"], p["status"]) for p in body["points"]] == [
        ("p1", "covered"),
        ("p2", "covered"),
    ]


def finished(client, world, policy):
    rid, _ = answered(client, world, policy)
    act(client, rid, "next", "feedback")
    return rid


def test_history_source_policy_filter(client, world):
    use(client)
    strict = [finished(client, world, "materials") for _ in range(2)]
    expanded = finished(client, world, "expanded")
    rows = history(client, source_policy="expanded").json()
    assert [(r["round_id"], r["source_policy"]) for r in rows] == [(expanded, "expanded")]
    rows = history(client, source_policy="materials").json()
    assert sorted(r["round_id"] for r in rows) == sorted(strict)
    assert all(r["source_policy"] == "materials" for r in rows)
    assert len(history(client).json()) == 3
    assert history(client, source_policy="web").status_code == 422


def count(client, call) -> tuple[int, int]:
    seen: list[str] = []

    def listener(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", listener)
    try:
        call()
    finally:
        event.remove(Engine, "before_cursor_execute", listener)
    writes = [s for s in seen if s.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}]
    return len(seen), len(writes)


def test_history_budget_with_source_filter(client, db, world):
    use(client)
    finished(client, world, "expanded")
    for _ in range(2):
        finished(client, world, "materials")

    def call():
        response = history(client, source_policy="expanded")
        assert response.status_code == 200 and len(response.json()) == 1

    small, writes = count(client, call)
    assert writes == 0
    for _ in range(4):
        finished(client, world, "materials")
    large, writes = count(client, call)
    assert writes == 0
    assert large == small
    assert db.query(QuestionVersion).count() == 8
