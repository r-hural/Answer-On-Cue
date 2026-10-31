from app.features.questions import validation as v
from app.features.questions.prompts import GeneratedQuestion
from app.features.questions.schemas import EssentialPoint, Rubric, SourceIssue

ALLOWED = {10, 11, 12}

V1_RUBRIC = {
    "essential_points": [
        {"id": "p1", "text": "Punkt 1", "weight": 0.5, "passage_ids": [10]},
        {"id": "p2", "text": "Punkt 2", "weight": 0.5, "alternatives": [], "passage_ids": [11]},
    ],
    "enrichment_points": [],
    "misconceptions": [],
    "major_error_rules": [],
    "model_answer": "Odpowiedź.",
    "expanded_explanation": None,
}


def point(n: int, basis: str = "source", ids: list[int] | None = None, weight: float = 1.0):
    return EssentialPoint(
        id=f"p{n}",
        text=f"Punkt {n}",
        weight=weight,
        passage_ids=[10] if ids is None else ids,
        basis=basis,
    )


def question(points, beyond=(), issues=()) -> GeneratedQuestion:
    return GeneratedQuestion(
        text="Omów reformy Klejstenesa w Atenach.",
        style="open",
        difficulty="recall",
        objective_index=0,
        rubric=Rubric(
            essential_points=list(points),
            model_answer="Odpowiedź.",
            beyond_material=list(beyond),
            source_issues=list(issues),
        ),
        suggested_prep_s=30,
        suggested_answer_s=120,
    )


def check(q, policy=None):
    if policy is None:
        return v.validate_question(q, ALLOWED, [])
    return v.validate_question(q, ALLOWED, [], policy=policy)


def test_old_rubric_parses_as_source():
    rubric = Rubric.model_validate(V1_RUBRIC)
    assert [p.basis for p in rubric.essential_points] == ["source", "source"]
    assert rubric.beyond_material == []
    assert rubric.source_issues == []


def test_materials_rejects_beyond_points():
    result = check(question([point(1), point(2, "expanded")]))
    assert result.question is None
    assert result.reason == v.REASON_BEYOND == "point beyond the materials"
    result = check(question([point(1), point(2, "mixed")]), "materials")
    assert result.reason == v.REASON_BEYOND
    result = check(question([point(1), point(2)], beyond=["Kontekst spoza tekstu"]))
    assert result.question is None
    assert result.reason == v.REASON_BEYOND_ANSWER == "model answer beyond the materials"


def test_materials_unchanged():
    result = check(question([point(1), point(2, ids=[999])]))
    assert result.reason == v.REASON_NO_SOURCE == "no source for a point"
    points = [point(1, weight=2.0), point(2), point(3)]
    for p in points:
        p.id = "x"
    result = check(question(points))
    assert result.reason is None
    cleaned = result.question.rubric.essential_points
    assert [p.weight for p in cleaned] == [0.5, 0.25, 0.25]
    assert [p.id for p in cleaned] == ["p1", "p2", "p3"]
    assert [p.basis for p in cleaned] == ["source"] * 3


def test_expanded_point_citations_cleared():
    result = check(question([point(1), point(2, "expanded", [1, 99])]), "expanded")
    assert result.reason is None
    cleaned = result.question.rubric.essential_points
    assert cleaned[1].passage_ids == []
    assert cleaned[1].basis == "expanded"
    result = check(question([point(1, ids=[99]), point(2, "expanded", [])]), "expanded")
    assert result.reason == v.REASON_NO_SOURCE
    result = check(question([point(1), point(2, "mixed", [11, 99])]), "expanded")
    assert result.reason is None
    assert result.question.rubric.essential_points[1].passage_ids == [11]
    result = check(question([point(1), point(2, "mixed", [99])]), "expanded")
    assert result.reason == v.REASON_NO_SOURCE


def test_expanded_beyond_material_stripped():
    q = question([point(1), point(2, "expanded", [])], beyond=["  Szerszy kontekst ", " ", ""])
    result = check(q, "expanded")
    assert result.reason is None
    assert result.question.rubric.beyond_material == ["Szerszy kontekst"]


def test_question_basis():
    def rubric(*bases, beyond=()):
        return question(
            [point(n, b, [] if b == "expanded" else [10]) for n, b in enumerate(bases, 1)],
            beyond=beyond,
        ).rubric

    assert v.question_basis(rubric("source", "source"), "expanded") == "source"
    assert v.question_basis(rubric("expanded", "expanded"), "expanded") == "expanded"
    assert v.question_basis(rubric("source", "expanded"), "expanded") == "mixed"
    assert v.question_basis(rubric("mixed", "mixed"), "expanded") == "mixed"
    assert v.question_basis(rubric("source", "source", beyond=["x"]), "expanded") == "mixed"
    assert v.question_basis(rubric("expanded", "expanded"), "materials") == "source"
    assert v.question_basis(rubric("source", "expanded", beyond=["x"]), "materials") == "source"


def test_source_issues_cleaned():
    issues = [
        SourceIssue(kind="contradiction", passage_ids=[10, 11, 99], note="Sprzeczne daty"),
        SourceIssue(kind="contradiction", passage_ids=[10, 98], note="Jedna strona"),
        SourceIssue(kind="unclear", passage_ids=[97], note="Niejasne"),
        SourceIssue(kind="insufficient", passage_ids=[], note="Za mało"),
    ]
    for policy in ("materials", "expanded"):
        result = check(question([point(1), point(2)], issues=issues), policy)
        assert result.reason is None
        cleaned = result.question.rubric.source_issues
        assert [(i.kind, i.passage_ids) for i in cleaned] == [
            ("contradiction", [10, 11]),
            ("unclear", [10]),
            ("unclear", []),
            ("insufficient", []),
        ]
        assert cleaned[0].note == "Sprzeczne daty"
