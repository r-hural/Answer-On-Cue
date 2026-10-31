from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_adaptive_extension import finish
from test_adaptive_support import build_world
from test_chapters_support import no_writes, two_chapter_world
from test_coverage_support import make_answer, make_round
from test_reports_history import count_statements
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.coverage import service as coverage
from app.features.library.models import Chapter
from app.features.rounds import extension, retry
from app.features.rounds import service as rounds
from app.features.rounds.models import Round, RoundItem
from app.features.rounds.schemas import RoundConfig, Scope
from app.features.rounds.selection import Candidate, eligible

NEED_CHAPTER = "Select a chapter."
FOREIGN_SCOPE = "The selected topics or documents do not belong to this subject."
NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def chapter_round(client, subject_id: int, chapter_id: int, count: int = 3, **config):
    body = {
        "subject_id": subject_id,
        "config": {
            "question_count": count,
            "scope": {"kind": "chapter", "chapter_id": chapter_id},
            **config,
        },
    }
    return client.post("/api/rounds", json=body)


def asked(db, round_id: int) -> set[int]:
    return set(
        db.scalars(select(RoundItem.question_version_id).where(RoundItem.round_id == round_id))
    )


def test_scope_validation():
    with pytest.raises(ValidationError, match=NEED_CHAPTER):
        Scope(kind="chapter")
    with pytest.raises(ValidationError):
        Scope(kind="subject", chapter_id=3)
    assert Scope(kind="chapter", chapter_id=3).chapter_id == 3
    assert Scope(kind="topics", topic_ids=[1], chapter_id=3).chapter_id == 3
    assert Scope(kind="documents", document_ids=[1]).chapter_id is None
    old = '{"scope": {"kind": "topics", "topic_ids": [4]}, "question_count": 5}'
    config = RoundConfig.model_validate_json(old)
    assert config.scope.chapter_id is None and config.scope.kind == "topics"


def test_chapter_round_asks_only_that_chapter(db, client):
    w = two_chapter_world(db)
    response = chapter_round(client, w["subject"].id, w["second"])
    assert response.status_code == 201, response.text
    assert response.json()["config"]["scope"] == {
        "kind": "chapter", "topic_ids": [], "document_ids": [], "chapter_id": w["second"],
    }  # fmt: skip
    assert asked(db, response.json()["id"]) <= {q.id for q in w["questions_B"]}
    first = chapter_round(client, w["subject"].id, w["default"], count=4)
    assert asked(db, first.json()["id"]) == {q.id for q in w["questions_A"]}

    too_many = chapter_round(client, w["subject"].id, w["second"], count=5)
    assert too_many.status_code == 409
    assert too_many.json()["detail"]["available"] == 4
    other = build_world(db, name="Biologia")
    foreign = db.scalars(select(Chapter.id).where(Chapter.subject_id == other.subject.id)).one()
    refused = chapter_round(client, w["subject"].id, foreign)
    assert refused.status_code == 422 and refused.json()["detail"] == FOREIGN_SCOPE
    missing = chapter_round(client, w["subject"].id, 99999)
    assert missing.status_code == 422 and missing.json()["detail"] == FOREIGN_SCOPE


def test_topics_round_keeps_chapter_context(db, client):
    w = two_chapter_world(db)
    body = {
        "subject_id": w["subject"].id,
        "config": {
            "question_count": 2,
            "scope": {"kind": "topics", "topic_ids": [w["TB"].id], "chapter_id": w["second"]},
        },
    }
    response = client.post("/api/rounds", json=body)
    assert response.status_code == 201, response.text
    assert response.json()["config"]["scope"]["chapter_id"] == w["second"]
    whole = {"subject_id": w["subject"].id, "config": {"question_count": 2,
             "scope": {"kind": "subject", "chapter_id": w["second"]}}}  # fmt: skip
    assert client.post("/api/rounds", json=whole).status_code == 422


def test_adaptive_chapter_round(db, client, monkeypatch):
    w = two_chapter_world(db)
    seen = []
    original = coverage.subject_coverage

    def spy(session, subject_id, layer="materials", chapter_id=None):
        seen.append((layer, chapter_id))
        return original(session, subject_id, layer, chapter_id=chapter_id)

    monkeypatch.setattr(rounds.coverage, "subject_coverage", spy)
    response = chapter_round(client, w["subject"].id, w["second"], round_type="adaptive")
    assert response.status_code == 201, response.text
    assert seen == [("materials", w["second"])]
    assert asked(db, response.json()["id"]) <= {q.id for q in w["questions_B"]}


def test_extension_and_retry_keep_the_chapter(db, client):
    parent = RoundConfig(scope=Scope(kind="chapter", chapter_id=7), question_count=5)
    assert extension._extension_config(parent, 3).scope == parent.scope
    assert retry._retry_config(parent, 2).scope == parent.scope

    world = build_world(db, per_objective=(3, 3, 3))
    default = db.scalars(select(Chapter.id).where(Chapter.subject_id == world.subject.id)).one()
    body = {
        "subject_id": world.subject.id,
        "config": {
            "question_count": 3,
            "style": "short",
            "retry_round": True,
            "scope": {"kind": "chapter", "chapter_id": default},
        },
    }
    round_id = client.post("/api/rounds", json=body).json()["id"]
    finish(db, round_id, [{"band": "weak"}, {"band": "good"}, {"band": "weak"}])
    child = client.post(f"/api/rounds/{round_id}/retry")
    assert child.status_code == 201, child.text
    assert child.json()["config"]["scope"]["chapter_id"] == default
    assert child.json()["config"]["scope"]["kind"] == "chapter"


def candidate(version_id: int, chapter_id: int | None) -> Candidate:
    return Candidate(
        question_version_id=version_id,
        family_id=version_id,
        primary_topic_id=version_id,
        style="short",
        difficulty="recall",
        document_ids=[],
        last_asked_round=None,
        chapter_id=chapter_id,
    )


def test_eligible_filters_chapter():
    pool = [candidate(1, None), candidate(2, 5), candidate(3, 6), candidate(4, 5)]
    by_chapter = RoundConfig(scope=Scope(kind="chapter", chapter_id=5))
    assert [c.question_version_id for c in eligible(pool, by_chapter)] == [2, 4]
    whole = RoundConfig(scope=Scope(kind="subject"))
    assert [c.question_version_id for c in eligible(pool, whole)] == [1, 2, 3, 4]
    topics = RoundConfig(scope=Scope(kind="topics", topic_ids=[1, 3], chapter_id=5))
    assert [c.question_version_id for c in eligible(pool, topics)] == [1, 3]


def answer_in(db, w, key: str, at: datetime, band: str = "good"):
    round_ = make_round(db, w["subject"].id, at=at)
    make_answer(db, round_, w[key][0], at=at, band=band)
    return round_


def test_coverage_by_chapter(db, client):
    w = two_chapter_world(db)
    url = f"/api/coverage/subjects/{w['subject'].id}"

    def get(**params):
        response = client.get(url, params=params)
        assert response.status_code == 200, response.text
        return response.json()

    answer_in(db, w, "questions_A", NOW - timedelta(days=1))
    body = get(chapter_id=w["second"])
    assert body["chapter_id"] == w["second"]
    assert [t["topic_id"] for t in body["topics"]] == [w["TB"].id]
    assert {t["chapter_id"] for t in body["topics"]} == {w["second"]}
    assert body["topic_counts"]["not_practised"] == 1 and body["line_total"] == 1
    whole = get()
    assert whole["chapter_id"] is None and len(whole["topics"]) == 2
    assert {t["topic_id"]: t["chapter_id"] for t in whole["topics"]} == {
        w["TA"].id: w["default"],
        w["TB"].id: w["second"],
    }
    first = get(chapter_id=w["default"])
    assert first["topic_counts"]["not_practised"] == 0

    filtered = count_statements(client, lambda: get(chapter_id=w["second"]))
    for n in range(4):
        answer_in(db, w, "questions_A", NOW - timedelta(days=2, hours=n))
    assert count_statements(client, lambda: get(chapter_id=w["second"])) == filtered
    assert count_statements(client, lambda: get()) == filtered
    no_writes(lambda: get(chapter_id=w["second"]))


def history_round(db, w, at, chapter_id=None, kind="chapter"):
    round_ = make_round(db, w["subject"].id, at=at)
    if kind != "subject":
        scope = (
            Scope(kind="chapter", chapter_id=chapter_id)
            if kind == "chapter"
            else Scope(kind="topics", topic_ids=[w["TA"].id], chapter_id=chapter_id)
        )
        config = RoundConfig.model_validate_json(round_.config_json)
        round_.config_json = config.model_copy(update={"scope": scope}).model_dump_json()
        db.commit()
    return round_


def test_history_by_chapter(db, client):
    w = two_chapter_world(db)
    db.get(Chapter, w["default"]).name = "Starożytna Grecja"
    db.commit()

    def get(**params):
        response = client.get(
            "/api/reports/history", params={"subject_id": w["subject"].id, **params}
        )
        assert response.status_code == 200, response.text
        return response.json()

    whole = history_round(db, w, NOW - timedelta(days=3), kind="subject")
    plain = count_statements(client, lambda: get())
    one = history_round(db, w, NOW - timedelta(days=2), w["default"])
    topics = history_round(db, w, NOW - timedelta(days=1), w["default"], kind="topics")
    two = history_round(db, w, NOW, w["second"])
    rows = get(chapter_id=w["default"])
    assert [r["round_id"] for r in rows] == [topics.id, one.id]
    assert {r["chapter_name"] for r in rows} == {"Starożytna Grecja"}
    assert {r["chapter_id"] for r in rows} == {w["default"]}
    everything = {r["round_id"]: r for r in get()}
    assert set(everything) == {whole.id, one.id, topics.id, two.id}
    assert everything[whole.id]["chapter_id"] is None
    assert everything[whole.id]["chapter_name"] is None
    assert everything[two.id]["chapter_name"] == "Rewolucja francuska"
    assert list(everything[two.id])[-2:] == ["chapter_id", "chapter_name"]

    db.delete(db.get(Round, one.id))
    db.delete(db.get(Round, topics.id))
    db.delete(db.get(Round, two.id))
    db.commit()
    assert count_statements(client, lambda: get()) == plain  # no chapter: no names statement
    no_writes(lambda: get(chapter_id=w["second"]))
