"""Practice "All questions" (stored `all`) and the readers of round and question policies."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_chapters_support import no_writes, weak_extension_round
from test_coverage_support import make_answer, make_objective_question, make_round
from test_reports_history import count_statements
from test_topics_support import make_subject, make_topic, make_version, upgrade

from app.db.session import SessionLocal
from app.features.backup import export
from app.features.coverage import service as coverage
from app.features.jobs.models import AiJob
from app.features.providers.registry import _generation_dep
from app.features.questions.models import QuestionVersion
from app.features.rounds import service as rounds
from app.features.rounds.models import Round, RoundItem
from app.features.rounds.schemas import RoundConfig, Scope
from app.features.rounds.selection import Candidate, eligible

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
POLICIES = ("materials", "expanded", "outside")


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    """Live materials, expanded and outside questions (2 each) on one topic."""
    subject = make_subject(db, "Historia")
    _, passages = make_version(db, subject.id, count=4, excluded=())
    topic = make_topic(db, subject.id, "Ateny", passages)
    questions = {
        policy: [
            make_objective_question(
                db,
                subject.id,
                topic.id,
                None,
                f"Pytanie {policy} {n}?",
                passage_ids=(passages[0].id,),
                source_policy=policy,
            )  # fmt: skip
            for n in range(2)
        ]
        for policy in POLICIES
    }
    db.commit()
    return {"subject": subject, "topic": topic, "questions": questions}


def stored_round(db, subject_id: int, policy: str, at: datetime) -> Round:
    row = make_round(db, subject_id, at=at)
    config = json.loads(row.config_json)
    config["source_policy"] = policy
    row.config_json = json.dumps(config)
    db.commit()
    return row


def finished_rounds(db, world) -> dict[str, Round]:
    """One finished round per stored policy, each with one answered item."""
    out = {}
    for n, (policy, question) in enumerate(
        (
            ("materials", world["questions"]["materials"][0]),
            ("expanded", world["questions"]["expanded"][0]),
            ("all", world["questions"]["outside"][0]),
        )
    ):
        at = NOW - timedelta(days=3 - n)
        row = stored_round(db, world["subject"].id, policy, at)
        make_answer(db, row, question, at=at, band="good", factual=60.0 + n)
        out[policy] = row
    return out


def new_round(client, world, policy: str, count: int):
    body = {
        "subject_id": world["subject"].id,
        "config": {"question_count": count, "scope": {"kind": "subject"}, "source_policy": policy},
    }
    return client.post("/api/rounds", json=body)


def asked_policies(db, round_id: int) -> list[str]:
    return sorted(
        db.scalars(
            select(QuestionVersion.source_policy)
            .join(RoundItem, RoundItem.question_version_id == QuestionVersion.id)
            .where(RoundItem.round_id == round_id)
        )
    )


def test_round_config_values(db, client, world):
    assert RoundConfig(scope=Scope(kind="subject"), source_policy="all").source_policy == "all"
    stored = RoundConfig.model_validate_json(
        '{"scope": {"kind": "subject"}, "source_policy": "expanded"}'
    )
    assert stored.source_policy == "expanded"
    assert RoundConfig(scope=Scope(kind="subject")).source_policy == "materials"
    with pytest.raises(ValidationError):
        RoundConfig(scope=Scope(kind="subject"), source_policy="web")
    assert new_round(client, world, "web", 2).status_code == 422


def test_all_round_draws_every_policy(db, client, world):
    response = new_round(client, world, "all", 6)
    assert response.status_code == 201, response.text
    assert asked_policies(db, response.json()["id"]) == sorted(
        [p for p in POLICIES for _ in range(2)]
    )
    strict = new_round(client, world, "materials", 2)
    assert asked_policies(db, strict.json()["id"]) == ["materials", "materials"]
    assert new_round(client, world, "materials", 3).status_code == 409

    def candidate(version_id: int, policy: str) -> Candidate:
        return Candidate(
            question_version_id=version_id, family_id=version_id, primary_topic_id=1,
            style="short", difficulty="recall", document_ids=[], last_asked_round=None,
            source_policy=policy,
        )  # fmt: skip

    pool = [candidate(n, p) for n, p in enumerate(POLICIES)]
    legacy_child = RoundConfig(scope=Scope(kind="subject"), source_policy="expanded",
                               round_type="retry")  # fmt: skip
    assert [c.source_policy for c in eligible(pool, legacy_child)] == ["expanded"]
    every = RoundConfig(scope=Scope(kind="subject"), source_policy="all")
    assert [c.source_policy for c in eligible(pool, every)] == list(POLICIES)


def test_adaptive_all_uses_all_layer(db, client, world, monkeypatch):
    seen = []
    original = coverage.subject_coverage

    def spy(session, subject_id, layer="materials", chapter_id=None):
        seen.append(layer)
        return original(session, subject_id, layer, chapter_id=chapter_id)

    monkeypatch.setattr(rounds.coverage, "subject_coverage", spy)
    body = {
        "subject_id": world["subject"].id,
        "config": {"question_count": 3, "scope": {"kind": "subject"}, "source_policy": "all",
                   "round_type": "adaptive"},
    }  # fmt: skip
    response = client.post("/api/rounds", json=body)
    assert response.status_code == 201, response.text
    assert seen == ["all"]


@pytest.mark.parametrize(
    ("round_policy", "job_policy"),
    [("all", "materials"), ("expanded", "expanded"), ("materials", "materials")],
)
def test_extension_generation_policy_mapping(db, client, round_policy, job_policy):
    from test_questions_support import QuestionFaker, all_good

    from app.features.providers.fakes import FakeTextGenerator

    fake = FakeTextGenerator({"questions.generate": QuestionFaker(), "questions.verify": all_good})
    client.app.dependency_overrides[_generation_dep] = lambda: fake
    # A legacy expanded parent asks expanded questions; the API still accepts that value.
    questions = "expanded" if round_policy == "expanded" else "materials"
    _, round_id = weak_extension_round(
        client, db, question_policy=questions, source_policy=round_policy
    )
    response = client.post(f"/api/rounds/{round_id}/extension/generate")
    assert response.status_code == 202, response.text
    params = json.loads(db.get(AiJob, response.json()["job_id"]).params_json)
    assert params["source_policy"] == job_policy


def test_coverage_all_layer(db, client, world):
    rows = finished_rounds(db, world)
    url = f"/api/coverage/subjects/{world['subject'].id}"

    def line(layer: str) -> dict:
        response = client.get(url, params={"layer": layer})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["layer"] == layer
        return body["topics"][0]["objectives"][0] if body["topics"] else {}

    assert (line("all")["answers"], line("all")["question_count"]) == (3, 6)
    assert (line("materials")["answers"], line("materials")["question_count"]) == (1, 2)
    assert (line("expanded")["answers"], line("expanded")["question_count"]) == (1, 2)
    assert client.get(url, params={"layer": "other"}).status_code == 422
    one = count_statements(client, lambda: line("all"))
    assert one == count_statements(client, lambda: line("materials"))
    no_writes(lambda: line("all"))
    assert rows


def test_history_policy_groups(db, client, world):
    rows = finished_rounds(db, world)

    def listed(policy=None):
        params = {"subject_id": world["subject"].id}
        if policy:
            params["source_policy"] = policy
        response = client.get("/api/reports/history", params=params)
        assert response.status_code == 200, response.text
        return {r["round_id"]: r["source_policy"] for r in response.json()}

    assert listed("all") == {rows["all"].id: "all", rows["expanded"].id: "expanded"}
    assert listed("materials") == {rows["materials"].id: "materials"}
    assert listed("expanded") == {rows["expanded"].id: "expanded"}
    assert set(listed().values()) == {"materials", "expanded", "all"}
    assert client.get("/api/reports/history", params={"source_policy": "web"}).status_code == 422


def test_trends_policy_groups(db, client, world):
    def points(policy=None):
        params = {"subject_id": world["subject"].id}
        if policy:
            params["source_policy"] = policy
        response = client.get("/api/trends", params=params)
        assert response.status_code == 200, response.text
        return [p["factual"] for p in response.json()["points"]]

    first = stored_round(db, world["subject"].id, "all", NOW - timedelta(days=9))
    make_answer(db, first, world["questions"]["outside"][1], at=first.started_at, band="good",
                factual=50.0)  # fmt: skip
    second = stored_round(db, world["subject"].id, "materials", NOW - timedelta(days=8))
    make_answer(db, second, world["questions"]["materials"][1], at=second.started_at,
                band="good", factual=55.0)  # fmt: skip
    small = count_statements(client, lambda: points("all"))
    finished_rounds(db, world)
    assert points("all") == [50.0, 61.0, 62.0]
    assert points("materials") == [55.0, 60.0]
    assert points() == [55.0, 60.0]
    assert points("expanded") == [61.0]
    for n in range(2):
        extra = stored_round(db, world["subject"].id, "all", NOW - timedelta(hours=n + 1))
        make_answer(db, extra, world["questions"]["outside"][0], at=extra.started_at,
                    band="good", factual=70.0)  # fmt: skip
    assert count_statements(client, lambda: points("all")) == small


def test_export_writes_raw_value(db, settings, world):
    import csv
    import io

    rows = finished_rounds(db, world)
    files, _, _ = export.render(db)

    def table(suffix: str) -> list[dict]:
        data = next(v for k, v in files.items() if k.endswith(suffix)).decode("utf-8-sig")
        return list(csv.DictReader(io.StringIO(data)))

    by_round = {int(r["round_id"]): r["source_policy"] for r in table("rounds.csv")}
    assert by_round[rows["all"].id] == "all"
    assert by_round[rows["expanded"].id] == "expanded"
    answers = {int(r["round_id"]): r["source_policy"] for r in table("answers.csv")}
    assert answers[rows["all"].id] == "all"
    markdown = next(v for k, v in files.items() if k.endswith(f"round-{rows['all'].id}.md"))
    assert "- Sources: all" in markdown.decode("utf-8").splitlines()
