from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from test_coverage_support import make_answer, make_round
from test_doc_versions_support import (
    V1,
    V2,
    cited_question,
    document_text,
    paragraph,
    replace_world,
    statements,
    writes,
)
from test_topics_support import make_subject, make_topic, upgrade

from app.db.session import SessionLocal
from app.features.backup import export
from app.features.library import service as library
from app.features.library.models import Document
from app.features.questions.models import QuestionVersion
from app.features.questions.validation import REASON_REMOVED_PASSAGE
from app.features.rounds import retry
from app.features.rounds.models import RoundItem
from app.features.rounds.schemas import RoundConfig
from app.features.topics.models import LearningObjective

AT = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
ZONE = None


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db, settings) -> dict:
    data = replace_world(db, settings)
    round_ = make_round(db, data["subject"], at=AT)
    config = RoundConfig.model_validate_json(round_.config_json)
    round_.config_json = config.model_copy(update={"retry_round": True}).model_dump_json()
    q1 = db.get(QuestionVersion, data["q"]["q1"])
    q2 = db.get(QuestionVersion, data["q"]["q2"])
    # Generated questions carry their objective; coverage keeps such evidence across versions.
    for question, topic in ((q1, "t1"), (q2, "t2")):
        question.objective_id = db.scalars(
            select(LearningObjective.id).where(LearningObjective.topic_id == data["topics"][topic])
        ).first()
    first = make_answer(db, round_, q1, at=AT, band="good")
    second = make_answer(db, round_, q2, at=AT + timedelta(minutes=2), band="weak")
    db.commit()
    data["round"] = round_.id
    data["attempts"] = [first.id, second.id]
    return data


def replace(client, world, paragraphs=V2) -> None:
    change = client.post(
        f"/api/documents/{world['a']}/versions/preview",
        files={"file": ("demokracja-2.txt", document_text(paragraphs), "text/plain")},
    ).json()
    applied = client.post(
        f"/api/documents/{world['a']}/versions/{change['token']}/apply",
        json={"fingerprint": change["fingerprint"]},
    )
    assert applied.status_code == 201, applied.text


def question(client, version_id: int) -> dict:
    response = client.get(f"/api/questions/{version_id}")
    assert response.status_code == 200, response.text
    return response.json()


def keep(client, version_id: int):
    return client.post(f"/api/questions/{version_id}/keep")


def v2_passages(db, world) -> list:
    db.expire_all()
    return list(db.get(Document, world["a"]).versions[-1].passages)


def cited(rubric: dict) -> list[int]:
    return [
        i
        for key in ("essential_points", "enrichment_points", "source_issues")
        for item in rubric[key]
        for i in item["passage_ids"]
    ]


def history_snapshot(client, db, world) -> dict:
    rid, subject = world["round"], world["subject"]
    db.expire_all()
    files, _, _ = export.render(db, ZONE)
    return {
        "round": client.get(f"/api/reports/rounds/{rid}").json(),
        "attempts": [client.get(f"/api/reports/attempts/{a}").json() for a in world["attempts"]],
        "history": client.get(f"/api/reports/history?subject_id={subject}").json(),
        "coverage": client.get(f"/api/coverage/subjects/{subject}").json(),
        "csv": {name: data for name, data in files.items() if name.endswith("answers.csv")},
    }


def test_history_is_unchanged_by_a_replace(client, db, world):
    before = history_snapshot(client, db, world)
    assert before["attempts"][1]["sources"], "the weak answer shows its sources"
    assert before["csv"]
    replace(client, world)
    assert keep(client, world["q"]["q2"]).status_code == 201
    assert history_snapshot(client, db, world) == before


def test_round_items_keep_their_question_version(client, db, world):
    def items(round_id):
        return list(
            db.scalars(
                select(RoundItem.question_version_id)
                .where(RoundItem.round_id == round_id)
                .order_by(RoundItem.ordinal)
            )
        )

    asked = items(world["round"])
    replace(client, world)
    assert keep(client, world["q"]["q2"]).status_code == 201
    db.expire_all()
    assert items(world["round"]) == asked
    state, created = retry.start(db, world["round"])
    assert created
    assert items(state.id) == [world["q"]["q2"]]


def test_needs_review_is_never_selected(client, db, world):
    replace(client, world)
    body = {"subject_id": world["subject"],
            "config": {"question_count": 2, "scope": {"kind": "subject"}}}  # fmt: skip
    started = client.post("/api/rounds", json=body)
    assert started.status_code == 201, started.text
    db.expire_all()
    round_id = started.json()["id"]
    asked = set(
        db.scalars(select(RoundItem.question_version_id).where(RoundItem.round_id == round_id))
    )
    q = world["q"]
    assert not asked & {q["q2"], q["q3"], q["q5"]}


def test_source_changes_in_question(client, db, world):
    replace(client, world)
    p = world["p"]
    v2 = v2_passages(db, world)
    q2 = question(client, world["q"]["q2"])
    assert q2["origin"] == "ai"
    assert len(q2["source_changes"]) == 1
    change = q2["source_changes"][0]
    assert (change["document_id"], change["document_title"]) == (world["a"], world["title"])
    assert (change["old_version"], change["new_version"]) == (1, 2)
    assert change["old"]["id"] == p[4] and change["old"]["text"] == V1[4].strip()
    assert change["new"]["id"] == v2[4].id and change["new"]["text"] == V2[4].strip()
    q3 = question(client, world["q"]["q3"])
    assert [(c["old"]["id"], c["new"]) for c in q3["source_changes"]] == [(p[7], None)]
    assert question(client, world["q"]["q1"])["source_changes"] == []


def test_keep_moves_citations(client, db, world):
    replace(client, world)
    old = question(client, world["q"]["q2"])
    v2 = v2_passages(db, world)
    response = keep(client, world["q"]["q2"])
    assert response.status_code == 201, response.text
    kept = response.json()
    assert (kept["version"], kept["status"], kept["review_note"]) == (2, "active", None)
    assert kept["family_id"] == old["family_id"] and kept["text"] == old["text"]
    assert kept["origin"] == "kept" and kept["source_changes"] == []
    assert cited(kept["rubric"]) == [v2[4].id, v2[4].id, v2[0].id]

    def without_citations(rubric: dict) -> dict:
        return {
            **rubric,
            "essential_points": [{**x, "passage_ids": []} for x in rubric["essential_points"]],
            "enrichment_points": [{**x, "passage_ids": []} for x in rubric["enrichment_points"]],
        }

    stripped, before = without_citations(kept["rubric"]), without_citations(old["rubric"])
    assert stripped == before
    db.expire_all()
    assert db.get(QuestionVersion, world["q"]["q2"]).status == "superseded"
    report = client.get(f"/api/reports/attempts/{world['attempts'][1]}").json()
    assert report["question_text"] == old["text"]
    assert report["sources"][0]["passage_id"] == world["p"][4]


def test_keep_refusals(client, db, world):
    not_needed = keep(client, world["q"]["q1"])
    assert not_needed.status_code == 409
    assert not_needed.json()["detail"] == "This question cites only current passages."
    replace(client, world)
    removed = keep(client, world["q"]["q3"])
    assert removed.status_code == 409
    assert removed.json()["detail"] == (
        "This question cites a passage that is no longer in the document. "
        "Edit it to choose another passage, or retire it."
    )
    assert keep(client, world["q"]["q2"]).status_code == 201
    latest = keep(client, world["q"]["q2"])
    assert latest.status_code == 409
    assert latest.json()["detail"] == "Edit the latest version of the question."
    assert client.post(f"/api/questions/{world['q']['q5']}/retire").status_code == 200
    retired = keep(client, world["q"]["q5"])
    assert retired.status_code == 409
    assert retired.json()["detail"] == "This question has been retired."


def test_edit_moves_citations(client, db, world):
    replace(client, world)
    v2 = v2_passages(db, world)
    q1 = question(client, world["q"]["q1"])
    edited = client.put(
        f"/api/questions/{world['q']['q1']}",
        json={"text": "Jakie instytucje miały wtedy Ateny?", "rubric": q1["rubric"]},
    )
    assert edited.status_code == 200, edited.text
    assert set(cited(edited.json()["rubric"])) == {v2[1].id}

    q3 = question(client, world["q"]["q3"])
    refused = client.put(f"/api/questions/{world['q']['q3']}", json={"rubric": q3["rubric"]})
    assert refused.status_code == 422
    assert REASON_REMOVED_PASSAGE in refused.json()["detail"]
    rubric = q3["rubric"]
    for point in rubric["essential_points"]:
        point["passage_ids"] = [v2[3].id]
    fixed = client.put(f"/api/questions/{world['q']['q3']}", json={"rubric": rubric})
    assert fixed.status_code == 200, fixed.text
    assert set(cited(fixed.json()["rubric"])) == {v2[3].id}


def test_edit_creates_versions(client, db, world):
    q1 = question(client, world["q"]["q1"])
    edited = client.put(
        f"/api/questions/{world['q']['q1']}",
        json={"text": "Jakie instytucje działały w Atenach?"},
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["version"] == q1["version"] + 1 and body["version_id"] != q1["version_id"]
    assert body["origin"] == "user"
    db.expire_all()
    assert db.get(QuestionVersion, q1["version_id"]).status == "superseded"
    report = client.get(f"/api/reports/attempts/{world['attempts'][0]}").json()
    assert report["question_text"] == q1["text"]


def test_subject_review_count(client, db, world):
    from app.features.topics.service import SPLIT_NOTE

    cited_question(
        db, world["subject"], world["topics"]["tb"], "Kto rządził Rzymem?",
        (world["pb"][1],), status="needs_review", note=SPLIT_NOTE,
    )  # fmt: skip

    def counts():
        one = client.get(f"/api/subjects/{world['subject']}").json()["source_review_count"]
        listed = {s["id"]: s["source_review_count"] for s in client.get("/api/subjects").json()}
        return one, listed[world["subject"]]

    assert counts() == (0, 0)
    replace(client, world)
    assert counts() == (3, 3)
    assert keep(client, world["q"]["q2"]).status_code == 201
    assert counts() == (2, 2)


# --- budgets -------------------------------------------------------------------------------


def _budget_question(db, settings, name: str, cited_count: int, versions: int) -> int:
    subject = make_subject(db, name)
    db.commit()
    paragraphs = [paragraph(n) for n in range(1, 6)]
    document = library.create_document(
        db, settings, subject.id, "a.txt", document_text(paragraphs), "Podręcznik"
    )
    passages = list(db.get(Document, document.id).versions[-1].passages)
    topic = make_topic(db, subject.id, "Temat", passages)
    question_ = cited_question(
        db, subject.id, topic.id, "Pytanie budżetowe?", tuple(p.id for p in passages[:cited_count])
    )
    for n in range(2, versions + 1):
        changed = [
            paragraph(k, f"wersji {n}") if k == 1 else t for k, t in enumerate(paragraphs, 1)
        ]
        library.add_version(db, settings, document.id, f"a{n}.txt", document_text(changed))
        paragraphs = changed
    return question_.id


def test_question_budget(client, db, settings):
    small = _budget_question(db, settings, "Mały", 1, 1)
    large = _budget_question(db, settings, "Duży", 4, 3)

    def measure(version_id):
        seen = statements(lambda: question(client, version_id))
        assert writes(seen) == []
        return len(seen)

    assert question(client, large)["source_changes"]
    assert measure(small) == measure(large)


def test_subject_budgets(client, db, settings):
    small = replace_world(db, settings)
    db.commit()
    measure_one = lambda: len(statements(lambda: client.get(f"/api/subjects/{small['subject']}")))  # noqa: E731
    measure_all = lambda: len(statements(lambda: client.get("/api/subjects")))  # noqa: E731
    one, many = measure_one(), measure_all()
    for n in range(6):
        cited_question(
            db, small["subject"], small["topics"]["t1"], f"Dodatkowe pytanie {n}?",
            (small["p"][0],), status="needs_review", note="source changed: „X” version 2",
        )  # fmt: skip
    for call in (lambda: client.get(f"/api/subjects/{small['subject']}"),
                 lambda: client.get("/api/subjects")):  # fmt: skip
        assert writes(statements(call)) == []
    assert client.get(f"/api/subjects/{small['subject']}").json()["source_review_count"] == 6
    assert (measure_one(), measure_all()) == (one, many)
