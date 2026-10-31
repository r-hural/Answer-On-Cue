import pytest
from test_questions_support import all_good
from test_topics_support import make_question, make_subject, make_topic, make_version, upgrade

from app.db.session import SessionLocal
from app.features.providers.fakes import FakeTextGenerator
from app.features.providers.registry import _generation_dep
from app.features.questions import validation as v
from app.features.questions.models import QuestionVersion, RubricVersion
from app.features.questions.prompts import GeneratedBatch, GeneratedQuestion
from app.features.questions.schemas import EnrichmentPoint, EssentialPoint, Rubric

ALLOWED = {10, 11, 12}


def question(
    text: str = "Omów reformy Klejstenesa w Atenach.",
    weights: tuple[float, ...] = (2.0, 1.0, 1.0),
    ids: tuple[list[int], ...] | None = None,
    style: str = "open",
    prep: int = 30,
    answer: int = 120,
) -> GeneratedQuestion:
    ids = ids or tuple([10] for _ in weights)
    return GeneratedQuestion(
        text=text,
        style=style,
        difficulty="recall",
        objective_index=0,
        rubric=Rubric(
            essential_points=[
                EssentialPoint(id=f"p{n}", text=f"Punkt {n}", weight=w, passage_ids=list(pids))
                for n, (w, pids) in enumerate(zip(weights, ids, strict=True))
            ],
            enrichment_points=[EnrichmentPoint(text="Dodatkowo", passage_ids=[11, 999])],
            model_answer="Odpowiedź.",
        ),
        suggested_prep_s=prep,
        suggested_answer_s=answer,
    )


def check(q, existing=()):
    return v.validate_question(q, ALLOWED, list(existing))


def test_weights_normalized_to_exactly_one():
    for weights in [(2.0, 1.0, 1.0), (1.0, 1.0, 1.0), (0.3, 0.3, 0.3, 0.1), (7, 3, 5, 1, 1, 1, 2)]:
        result = check(question(weights=weights))
        assert result.reason is None
        normalized = [p.weight for p in result.question.rubric.essential_points]
        assert round(sum(normalized), 10) == 1.0
        assert all(round(w, 4) == w for w in normalized)
    thirds = check(question(weights=(1.0, 1.0, 1.0))).question.rubric.essential_points
    assert [p.weight for p in thirds] == [0.3334, 0.3333, 0.3333]
    assert v.normalize_weights([2.0, 1.0, 1.0]) == [0.5, 0.25, 0.25]


@pytest.mark.parametrize(
    "weights", [(1.0, 0.0), (1.0, -0.5, 1.0), (1.0, float("nan")), (1.0, float("inf"))]
)
def test_zero_or_negative_weight_rejected(weights):
    result = check(question(weights=weights))
    assert result.question is None
    assert result.reason == v.REASON_WEIGHT


@pytest.mark.parametrize("count", [0, 1, 9])
def test_point_count_bounds(count):
    result = check(question(weights=(1.0,) * count))
    assert result.question is None
    assert result.reason == v.REASON_POINTS
    assert check(question(weights=(1.0,) * 2)).reason is None
    assert check(question(weights=(1.0,) * 8)).reason is None


def test_invalid_passage_ids_dropped():
    result = check(question(weights=(1.0, 1.0), ids=([10, 999], [11, 12, 4242])))
    assert result.reason is None
    assert [p.passage_ids for p in result.question.rubric.essential_points] == [[10], [11, 12]]
    assert result.question.rubric.enrichment_points[0].passage_ids == [11]


def test_point_without_source_rejects_question():
    result = check(question(weights=(1.0, 1.0), ids=([10], [999])))
    assert result.question is None
    assert result.reason == "no source for a point"
    assert check(question(weights=(1.0, 1.0), ids=([10], []))).reason == "no source for a point"


def test_excluded_passage_id_dropped():
    # 13 is a passage of the topic that the user excluded: it is not in the allowed set.
    result = check(question(weights=(1.0, 1.0), ids=([10, 13], [13])))
    assert result.question is None
    assert result.reason == v.REASON_NO_SOURCE
    kept = check(question(weights=(1.0, 1.0), ids=([10, 13], [11])))
    assert kept.question.rubric.essential_points[0].passage_ids == [10]


def test_near_duplicate_vs_existing_rejected():
    existing = ["Omów reformy Klejstenesa w Atenach!"]
    result = check(question(text="omów  reformy Klejstenesa w Atenach."), existing)
    assert result.question is None and result.reason == "duplicate"
    close = check(question(text="Omów reformę Klejstenesa w Atenach."), existing)
    assert close.reason == "duplicate"
    assert check(question(text="Kim byli heloci w Sparcie?"), existing).reason is None
    assert v.normalize_text("  Omów,  reformy!\nKLEJSTENESA? ") == "omów reformy klejstenesa"


def test_near_duplicate_within_batch_rejected(db, api, fake, world):
    subject, _, a = world
    fake.responses["questions.generate"] = lambda m: GeneratedBatch(
        questions=[
            question(ids=_ids(world)),
            question(text="Omów reformy Klejstenesa w Atenach!", ids=_ids(world)),
        ]
    )
    job_id = _generate(api, subject.id, a.id, 2)
    assert db.query(QuestionVersion).count() == 1
    assert api.get(f"/api/jobs/{job_id}").json()["summary"]["rejected"] == {"duplicate": 1}


@pytest.mark.parametrize(
    ("style", "prep", "answer", "expected"),
    [
        ("short", -5, 5, (0, 15)),
        ("short", 100, 200, (60, 90)),
        ("open", 500, 10, (120, 45)),
        ("open", 60, 1000, (60, 300)),
        ("open", 30, 120, (30, 120)),
    ],
)
def test_durations_clamped_per_style(style, prep, answer, expected):
    result = check(question(style=style, prep=prep, answer=answer))
    assert (result.question.suggested_prep_s, result.question.suggested_answer_s) == expected


def test_text_length_bounds():
    assert check(question(text="   Za krótko  ")).reason == v.REASON_TEXT
    assert check(question(text="x" * 401)).reason == v.REASON_TEXT
    ok = check(question(text="   Czym była eklezja?   "))
    assert ok.reason is None and ok.question.text == "Czym była eklezja?"
    assert check(question(text="Czym była?" + "x" * 390)).reason is None  # exactly 400


# End to end through the job.


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


@pytest.fixture
def world(db):
    subject = make_subject(db)
    _, passages = make_version(db, subject.id, count=4, excluded=(3,))
    topic = make_topic(db, subject.id, "Ateny", passages, ("Wyjaśnić demokrację",))
    return subject, passages, topic


@pytest.fixture
def fake():
    return FakeTextGenerator({"questions.verify": all_good})


@pytest.fixture
def api(client, fake):
    client.app.dependency_overrides[_generation_dep] = lambda: fake
    return client


def _ids(world) -> tuple[list[int], ...]:
    _, passages, _ = world
    return ([passages[0].id], [passages[1].id], [passages[2].id])


def _generate(api, subject_id, topic_id, per_topic) -> int:
    response = api.post(
        "/api/questions/generate",
        json={"subject_id": subject_id, "topic_ids": [topic_id], "per_topic": per_topic,
              "style": "open", "difficulty": "recall"},
    )  # fmt: skip
    assert response.status_code == 202, response.text
    return response.json()["job_id"]


def test_job_summary_counts_rejections_by_reason(db, api, fake, world):
    subject, passages, topic = world
    make_question(db, subject.id, topic.id, text="Kim byli heloci i jaka była ich rola?")
    good, excluded = _ids(world), [passages[3].id]
    fake.responses["questions.generate"] = lambda m: GeneratedBatch(
        questions=[
            question(text="Omów reformy Klejstenesa w Atenach.", ids=good),
            question(text="Kim byli heloci i jaka była ich rola?", ids=good),
            question(text="Wyjaśnij pojęcie ostracyzmu.", ids=(good[0], excluded, good[2])),
            question(text="Krótko", ids=good),
            question(text="Jakie uprawnienia miała eklezja?", weights=(1.0, 0.0, 1.0), ids=good),
        ]
    )
    job_id = _generate(api, subject.id, topic.id, 5)
    summary = api.get(f"/api/jobs/{job_id}").json()["summary"]
    assert summary["saved"] == 1
    assert summary["rejected"] == {
        "duplicate": 1,
        "no source for a point": 1,
        v.REASON_TEXT: 1,
        v.REASON_WEIGHT: 1,
    }
    saved = db.query(QuestionVersion).filter(QuestionVersion.text.like("Omów%")).one()
    rubric = Rubric.model_validate_json(
        db.query(RubricVersion).filter_by(question_version_id=saved.id).one().rubric_json
    )
    assert [p.weight for p in rubric.essential_points] == [0.5, 0.25, 0.25]
    verify_calls = [c for c in fake.calls if c["task"] == "questions.verify"]
    assert verify_calls[0]["messages"][0].content.count('<question index="') == 1


def test_unique_point_ids_kept():
    result = check(question(weights=(2.0, 1.0, 1.0)))
    assert [p.id for p in result.question.rubric.essential_points] == ["p0", "p1", "p2"]


@pytest.mark.parametrize("ids", [("p1", "p1", "p2"), ("p1", " ", "p3"), ("a", "A ", "b")])
def test_duplicate_or_blank_point_ids_renumbered(ids):
    # Assessment judges every point by id, so ids must be unique within a rubric.
    q = question(weights=(2.0, 1.0, 1.0))
    for point, point_id in zip(q.rubric.essential_points, ids, strict=True):
        point.id = point_id
    result = check(q)
    assert result.reason is None
    assert [p.id for p in result.question.rubric.essential_points] == ["p1", "p2", "p3"]
