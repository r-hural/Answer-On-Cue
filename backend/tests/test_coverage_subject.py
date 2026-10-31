from datetime import UTC, datetime, timedelta

import pytest
from test_coverage_support import (
    make_answer,
    make_objective,
    make_objective_question,
    make_round,
)
from test_topics_support import make_subject, make_topic, make_version, upgrade

from app.db.session import SessionLocal
from app.features.questions.models import QuestionFamily
from app.features.rounds.models import Round
from app.features.topics.models import Topic

ANCHOR = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
STATUSES = ("strong", "improving", "needs_work", "not_practised")


def day(n: int) -> datetime:
    """d-N at 12:00 UTC."""
    return ANCHOR - timedelta(days=n)


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


class Scene:
    """A subject with one topic per entry of `objectives`, each objective with one question."""

    def __init__(
        self, db, objectives=(("Temat A", ("O1", "O2")), ("Temat B", ("O3", "O4"))), name="Historia"
    ):
        self.db = db
        self.subject = make_subject(db, name)
        _, self.passages = make_version(db, self.subject.id, count=4, excluded=())
        self.topics = {}
        self.objectives = {}
        self.questions = {}
        for ordinal, (title, texts) in enumerate(objectives):
            topic = make_topic(db, self.subject.id, title, self.passages[:2], ordinal=ordinal)
            self.topics[title] = topic
            for n, text in enumerate(texts):
                objective = make_objective(db, topic, text, n)
                self.objectives[text] = objective
                self.questions[text] = make_objective_question(
                    db, self.subject.id, topic.id, objective.id, f"Pytanie o {text}?"
                )

    def round(self, at, **kwargs) -> Round:
        return make_round(self.db, self.subject.id, at=at, **kwargs)

    def answer(self, key, at, band, factual=None, *, round_=None, **kwargs):
        round_ = round_ or self.round(at)
        return make_answer(
            self.db, round_, self.questions[key], at=at, band=band, factual=factual, **kwargs
        )

    def coverage(self, client) -> dict:
        response = client.get(f"/api/coverage/subjects/{self.subject.id}")
        assert response.status_code == 200, response.text
        return response.json()

    def line(self, client, text):
        found = [
            line
            for topic in self.coverage(client)["topics"]
            for line in topic["objectives"]
            if line["text"] == text
        ]
        assert len(found) == 1, found
        return found[0]


@pytest.fixture
def scene(db):
    return Scene(db)


def test_subject_coverage_lines_and_counts(client, scene):
    scene.answer("O1", day(3), "needs_improvement", 51)
    scene.answer("O2", day(5), "very_good", 88)
    scene.answer("O2", day(2), "very_good", 84)
    scene.answer("O3", day(1), "good", 66)
    body = scene.coverage(client)
    assert body["subject_name"] == "Historia"
    assert [t["title"] for t in body["topics"]] == ["Temat A", "Temat B"]
    assert [[o["text"] for o in t["objectives"]] for t in body["topics"]] == [
        ["O1", "O2"],
        ["O3", "O4"],
    ]
    status = {text: scene.line(client, text)["status"] for text in ("O1", "O2", "O3", "O4")}
    assert status == {
        "O1": "needs_work",
        "O2": "strong",
        "O3": "improving",
        "O4": "not_practised",
    }
    assert body["line_total"] == 4
    assert body["line_counts"] == {
        "strong": 1,
        "improving": 1,
        "needs_work": 1,
        "not_practised": 1,
    }
    assert body["topic_counts"] == {
        "strong": 0,
        "improving": 1,
        "needs_work": 1,
        "not_practised": 0,
    }
    assert [t["status"] for t in body["topics"]] == ["needs_work", "improving"]
    o2 = scene.line(client, "O2")
    assert o2["answers"] == 2 and o2["independent_answers"] == 2
    assert o2["recent_factual"] == [84, 88]
    assert o2["last_practised_at"].startswith("2026-10-18T12:00:00")
    assert o2["median_preparation_used_ms"] == 8000
    assert o2["question_count"] == 1
    assert o2["deciding"]["factual"] == 84 and o2["deciding"]["band"] == "very_good"
    o4 = scene.line(client, "O4")
    assert o4["answers"] == 0 and o4["last_practised_at"] is None
    assert o4["recent_factual"] == [] and o4["deciding"] is None
    assert o4["median_preparation_used_ms"] is None


def test_dont_know_and_skip_count_as_attempted_weak(client, scene):
    scene.answer("O4", day(2), None, outcome="dont_know")
    o4 = scene.line(client, "O4")
    assert o4["status"] == "needs_work" and o4["answers"] == 1 and o4["recent_factual"] == [0]
    scene.answer("O3", day(2), None, outcome="skipped")
    assert scene.line(client, "O3")["status"] == "needs_work"


def test_not_evidence(client, scene):
    scene = Scene(scene.db, (("Temat C", ("F1", "F2", "F3", "F4", "F5")),), name="Inny")
    scene.answer("F1", day(1), None, outcome="technical_failure")
    scene.answer("F2", day(1), None, outcome="interrupted")
    scene.answer("F3", day(1), None, processing="processing")
    scene.answer("F4", day(1), None, processing="failed")
    scene.answer("F5", day(1), None, assessment_status="no_speech")
    for text in ("F1", "F2", "F3", "F4", "F5"):
        line = scene.line(client, text)
        assert line["status"] == "not_practised" and line["answers"] == 0, text


def test_exam_round_running_is_left_out(client, scene):
    for status in ("active", "paused"):
        exam = scene.round(day(1), timing="exam", status=status, current_index=5)
        scene.answer("O1", day(1), "very_good", round_=exam)
        assert scene.line(client, "O1")["status"] == "not_practised", status
        exam.status = "finished"
        scene.db.commit()
        assert scene.line(client, "O1")["status"] == "improving"
        exam.status = "abandoned"
        scene.db.commit()
        assert scene.line(client, "O1")["answers"] == 1
        scene.db.delete(exam)
        scene.db.commit()
    live = scene.round(day(1), status="active", phase="feedback")
    scene.answer("O2", day(1), "very_good", round_=live)
    assert scene.line(client, "O2")["status"] == "improving"


def test_current_item_waits_for_its_feedback(client, scene):
    practice = scene.round(day(1), status="active", phase="processing", current_index=0)
    scene.answer("O1", day(1), "very_good", round_=practice)
    assert scene.line(client, "O1")["answers"] == 0
    practice.phase = "feedback"
    scene.db.commit()
    assert scene.line(client, "O1")["answers"] == 1
    practice.phase = "question"
    practice.current_index = 1
    scene.db.commit()
    assert scene.line(client, "O1")["answers"] == 1
    practice.status = "paused"
    practice.phase = "recording"
    practice.current_index = 0
    scene.db.commit()
    assert scene.line(client, "O1")["answers"] == 0


def test_followups_and_expanded_are_left_out(client, scene):
    round_ = scene.round(day(1))
    make_answer(
        scene.db, round_, scene.questions["O1"], at=day(1), band="very_good", role="followup"
    )
    make_answer(
        scene.db, round_, scene.questions["O1"], at=day(1), band="very_good", kind="followup"
    )
    expanded = make_objective_question(
        scene.db,
        scene.subject.id,
        scene.topics["Temat A"].id,
        scene.objectives["O2"].id,
        "Pytanie rozszerzone?",
        source_policy="expanded",
    )
    make_answer(scene.db, round_, expanded, at=day(1), band="very_good")
    assert scene.line(client, "O1")["answers"] == 0
    o2 = scene.line(client, "O2")
    assert o2["answers"] == 0 and o2["question_count"] == 1


def test_assisted_flag(client, scene):
    scene.answer("O1", day(3), "needs_improvement", 51)
    scene.answer("O1", day(2), "good", 66, kind="immediate")
    o1 = scene.line(client, "O1")
    assert o1["status"] == "improving" and o1["assisted"] is True
    assert o1["answers"] == 2 and o1["independent_answers"] == 1
    assert o1["recent_factual"] == [51]
    scene.answer("O1", day(1), "weak", 10)
    o1 = scene.line(client, "O1")
    assert o1["status"] == "needs_work" and o1["assisted"] is False


def test_whole_topic_line(client, db):
    scene = Scene(db, (("Temat A", ("OA",)), ("Temat C", ("OC",))))
    topic_a, topic_c = scene.topics["Temat A"], scene.topics["Temat C"]
    loose = make_objective_question(db, scene.subject.id, topic_a.id, None, "Pytanie ogólne?")
    empty = make_topic(db, scene.subject.id, "Temat pusty", ordinal=5)
    make_objective_question(
        db, scene.subject.id, topic_c.id, None, "Pytanie dodatkowe?", status="followup"
    )
    make_objective_question(
        db, scene.subject.id, topic_c.id, None, "Pytanie wycofane?", status="retired"
    )
    scene.answer("OC", day(2), "good")
    body = scene.coverage(client)
    by_topic = {t["title"]: t["objectives"] for t in body["topics"]}
    assert [o["objective_id"] for o in by_topic["Temat C"]] == [scene.objectives["OC"].id]
    assert [o["text"] for o in by_topic["Temat A"]] == ["OA", None]
    assert by_topic["Temat A"][1]["status"] == "not_practised"
    assert by_topic["Temat A"][1]["question_count"] == 1
    (only,) = by_topic["Temat pusty"]
    assert only["objective_id"] is None and only["status"] == "not_practised"
    assert empty.id in [t["topic_id"] for t in body["topics"]]
    assert body["line_total"] == 4
    assert body["line_counts"]["not_practised"] == 3
    make_answer(db, scene.round(day(1)), loose, at=day(1), band="good")
    line = {o["text"]: o for o in scene.coverage(client)["topics"][0]["objectives"]}[None]
    assert line["status"] == "improving" and line["answers"] == 1


def test_retired_question_still_counts_removed_topic_hidden(client, db):
    scene = Scene(db, (("Temat A", ("OA",)), ("Temat Z", ("OZ",))))
    scene.answer("OA", day(2), "good")
    scene.questions["OA"].status = "retired"
    db.commit()
    line = scene.line(client, "OA")
    assert line["status"] == "improving" and line["answers"] == 1 and line["question_count"] == 0
    scene.answer("OZ", day(2), "good")
    db.get(Topic, scene.topics["Temat Z"].id).status = "removed"
    orphan = make_objective_question(db, scene.subject.id, None, None, "Bez tematu?")
    make_answer(db, scene.round(day(1)), orphan, at=day(1), band="good")
    db.commit()
    body = scene.coverage(client)
    assert [t["title"] for t in body["topics"]] == ["Temat A"]
    assert body["line_total"] == 1 and body["topic_counts"]["improving"] == 1
    assert db.get(QuestionFamily, orphan.family_id).primary_topic_id is None


def test_band_snapshot_not_rebanded(client, scene):
    scene.answer("O3", day(1), "good", 66)
    response = client.put("/api/settings", json={"factual_band_bounds": [30, 50, 65]})
    assert response.status_code == 200, response.text
    # Under the new table 66 would be Very good, but the stored band stays Good.
    line = scene.line(client, "O3")
    assert line["status"] == "improving" and line["deciding"]["band"] == "good"


def test_unknown_subject_404(client, db):
    response = client.get("/api/coverage/subjects/999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Subject not found."


def test_builder_rounds_render_in_reports(client, scene):
    scene.answer("O1", day(2), "needs_improvement", 51)
    scene.answer("O2", day(1), "very_good", 88)
    rows = client.get("/api/reports/history")
    assert rows.status_code == 200, rows.text
    assert len(rows.json()) == 2
    for row in rows.json():
        report = client.get(f"/api/reports/rounds/{row['round_id']}")
        assert report.status_code == 200, report.text
