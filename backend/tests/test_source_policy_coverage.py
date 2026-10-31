"""The expanded coverage layer: separate from the materials map."""

from datetime import timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from test_coverage_subject import ANCHOR, Scene, day
from test_coverage_support import make_answer, make_objective_question
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.coverage import service

WRITES = ("INSERT", "UPDATE", "DELETE")


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def scene(db):
    """Temat A (O1, O2), Temat B (O3, O4), materials questions; plus an expanded one on O1."""
    scene = Scene(db)
    scene.questions["O1+"] = make_objective_question(
        db,
        scene.subject.id,
        scene.topics["Temat A"].id,
        scene.objectives["O1"].id,
        "Pytanie rozszerzone o O1?",
        source_policy="expanded",
    )
    return scene


def subject(client, scene, layer=None):
    params = {"layer": layer} if layer else {}
    response = client.get(f"/api/coverage/subjects/{scene.subject.id}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def topic(client, scene, title, layer=None):
    params = {"layer": layer} if layer else {}
    response = client.get(f"/api/coverage/topics/{scene.topics[title].id}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def statements(call) -> list[str]:
    seen = []

    def listener(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement.lstrip().upper())

    event.listen(Engine, "before_cursor_execute", listener)
    try:
        call()
    finally:
        event.remove(Engine, "before_cursor_execute", listener)
    return seen


def test_expanded_evidence_never_changes_materials_map(client, db, scene):
    scene.answer("O1", day(4), "needs_improvement", 45)
    before = subject(client, scene)
    before_topic = topic(client, scene, "Temat A")
    scene.answer("O1+", day(3), "very_good", 90)
    scene.answer("O1+", day(1), "very_good", 92)
    after = subject(client, scene)
    assert after == before
    assert topic(client, scene, "Temat A") == before_topic
    assert after["layer"] == "materials"
    expanded = subject(client, scene, "expanded")
    (line,) = expanded["topics"][0]["objectives"]
    assert (line["text"], line["status"], line["answers"]) == ("O1", "strong", 2)
    assert line["question_count"] == 1
    materials_o1 = after["topics"][0]["objectives"][0]
    assert (materials_o1["status"], materials_o1["answers"], materials_o1["question_count"]) == (
        "needs_work",
        1,
        1,
    )


def test_expanded_layer_lists_only_expanded_lines(client, db, scene):
    body = subject(client, scene, "expanded")
    assert body["layer"] == "expanded"
    assert [t["title"] for t in body["topics"]] == ["Temat A"]
    assert [o["text"] for o in body["topics"][0]["objectives"]] == ["O1"]
    assert body["line_total"] == 1
    assert body["line_counts"] == {
        "strong": 0,
        "improving": 0,
        "needs_work": 0,
        "not_practised": 1,
    }
    assert body["topic_counts"]["not_practised"] == 1 and sum(body["topic_counts"].values()) == 1

    make_objective_question(
        db, scene.subject.id, scene.topics["Temat B"].id, None, "Rozszerzone o temacie B?",
        source_policy="expanded",
    )  # fmt: skip
    body = subject(client, scene, "expanded")
    assert [t["title"] for t in body["topics"]] == ["Temat A", "Temat B"]
    assert [o["objective_id"] for o in body["topics"][1]["objectives"]] == [None]
    assert body["line_total"] == 2
    materials = subject(client, scene)
    assert [o["objective_id"] for o in materials["topics"][1]["objectives"]] == [
        scene.objectives["O3"].id,
        scene.objectives["O4"].id,
    ]


def test_topic_detail_expanded_layer(client, db, scene):
    scene.answer("O1", day(2), "good", 66)
    scene.answer("O1+", day(1), "weak", 20)
    detail = topic(client, scene, "Temat A", "expanded")
    assert detail["layer"] == "expanded"
    assert [h["question_text"] for h in detail["history"]] == ["Pytanie rozszerzone o O1?"]
    lines = subject(client, scene, "expanded")["topics"][0]["objectives"]
    assert detail["objectives"] == lines
    assert detail["question_count"] == 1
    materials = topic(client, scene, "Temat A")
    assert materials["layer"] == "materials"
    assert [h["question_text"] for h in materials["history"]] == ["Pytanie o O1?"]
    assert [o["text"] for o in materials["objectives"]] == ["O1", "O2"]


def test_layer_validation_and_budget(client, db, scene):
    sid, tid = scene.subject.id, scene.topics["Temat A"].id
    assert client.get(f"/api/coverage/subjects/{sid}", params={"layer": "other"}).status_code == 422
    assert client.get(f"/api/coverage/topics/{tid}", params={"layer": "other"}).status_code == 422

    def get_subject():
        assert client.get(f"/api/coverage/subjects/{sid}?layer=expanded").status_code == 200

    def get_topic():
        assert client.get(f"/api/coverage/topics/{tid}?layer=expanded").status_code == 200

    scene.answer("O1+", day(1), "good")
    small = (statements(get_subject), statements(get_topic))
    for n in range(5):
        at = ANCHOR - timedelta(days=n, hours=1)
        round_ = scene.round(at)
        for key in ("O1+", "O1", "O2", "O1+"):
            make_answer(db, round_, scene.questions[key], at=at, band="good")
    large = (statements(get_subject), statements(get_topic))
    assert [len(s) for s in small] == [len(s) for s in large]
    assert len(small[0]) <= 12 and len(small[1]) <= 14
    for seen in small + large:
        assert not [s for s in seen if s.startswith(WRITES)]


def test_service_default_layer_is_materials(db, scene):
    assert service.subject_coverage(db, scene.subject.id).layer == "materials"
    assert service.topic_detail(db, scene.topics["Temat A"].id).layer == "materials"
