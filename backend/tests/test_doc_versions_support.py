"""Builders for the document versions tests (no tests here); the older support files stay
unedited."""

import importlib.util

from sqlalchemy import text
from test_chapters_support import migration_config  # noqa: F401  (re-exported)
from test_topics_support import BACKEND

BASE_REVISION = "0013_chapters"


def doc_versions_migration():
    path = BACKEND / "migrations" / "versions" / "0015_doc_versions.py"
    spec = importlib.util.spec_from_file_location("migration_0015_doc_versions", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def raw_objective(conn, topic_id: int, text_: str = "Cel") -> int:
    return conn.execute(
        text(
            "INSERT INTO learning_objective (topic_id, text, ordinal) VALUES (:t, :x, 0)"
            " RETURNING id"
        ),
        {"t": topic_id, "x": text_},
    ).scalar_one()


def objective_document(conn, objective_id: int) -> int | None:
    return conn.execute(
        text("SELECT document_id FROM learning_objective WHERE id = :i"), {"i": objective_id}
    ).scalar_one()


def statements(call) -> list[str]:
    """Every SQL statement `call` runs."""
    from sqlalchemy import Engine, event

    seen: list[str] = []

    def listener(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", listener)
    try:
        call()
    finally:
        event.remove(Engine, "before_cursor_execute", listener)
    return seen


def writes(seen: list[str]) -> list[str]:
    return [s for s in seen if s.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}]


def paragraph(n: int, word: str = "demokracji") -> str:
    """About 700 characters, so every paragraph is a passage of its own."""
    return f"Akapit {n}. " + f"Zdanie {n} o ateńskiej {word} i instytucjach polis. " * 13


def document_text(paragraphs: list[str]) -> bytes:
    return "\n\n".join(paragraphs).encode()


V1 = [paragraph(n) for n in range(1, 9)]
# P5 reworded, P8 deleted, a new paragraph after P6; P3 and P7 (excluded) unchanged.
V2 = [*V1[:4], paragraph(5, "republice"), V1[5], paragraph(9), V1[6]]


def cited_question(
    session,
    subject_id: int,
    topic_id: int | None,
    text: str,
    essential: tuple[int, ...] = (),
    enrichment: tuple[int, ...] = (),
    status: str = "active",
    note: str | None = None,
    policy: str = "materials",
):
    """A question version whose essential points cite `essential` (both points) and whose
    enrichment point cites `enrichment`."""
    import json

    from app.features.questions.models import QuestionFamily, QuestionVersion, RubricVersion

    family = QuestionFamily(subject_id=subject_id, primary_topic_id=topic_id)
    session.add(family)
    session.flush()
    question = QuestionVersion(
        family_id=family.id,
        version=1,
        text=text,
        style="open",
        difficulty="recall",
        source_policy=policy,
        secondary_topic_ids="[]",
        suggested_prep_s=30,
        suggested_answer_s=120,
        status=status,
        review_note=note,
        provenance_json=json.dumps({"origin": "ai"}),
    )
    session.add(question)
    session.flush()
    basis = "expanded" if policy == "outside" else "source"
    rubric = {
        "essential_points": [
            {"id": "p1", "text": "Zgromadzenie ludowe", "weight": 0.5,
             "passage_ids": list(essential), "basis": basis},
            {"id": "p2", "text": "Rada Pięciuset", "weight": 0.5,
             "passage_ids": list(essential), "basis": basis},
        ],
        "enrichment_points": [
            {"text": "Ostracyzm", "passage_ids": list(enrichment)}
        ] if enrichment else [],
        "model_answer": "Demokracja ateńska opierała się na zgromadzeniu i radzie.",
    }  # fmt: skip
    session.add(RubricVersion(question_version_id=question.id, rubric_json=json.dumps(rubric)))
    session.commit()
    return question


def replace_world(db, settings) -> dict:
    """ "Demokracja ateńska": document A version 1 from 8 paragraphs (P1–P8, P7 excluded),
    topics T1 (P1–P3), T2 (P4–P6), T3 (P8); Q1 cites P2, Q2 P5 (essential) and P1
    (enrichment), Q3 P8, Q4 "outside" cites nothing, Q5 already needs_review citing P5;
    document B with its own topic and question QB."""
    from test_topics_support import make_subject, make_topic

    from app.features.library import service as library
    from app.features.library.models import Document, SourcePassage

    subject = make_subject(db, "Demokracja ateńska")
    db.commit()
    a = library.create_document(
        db, settings, subject.id, "demokracja.txt", document_text(V1), "Demokracja w Atenach"
    )
    b = library.create_document(
        db,
        settings,
        subject.id,
        "rzym.txt",
        document_text([paragraph(n, "republice rzymskiej") for n in range(1, 3)]),
        "Rzym",
    )
    p = list(db.get(Document, a.id).versions[-1].passages)
    pb = list(db.get(Document, b.id).versions[-1].passages)
    assert len(p) == 8 and len(pb) == 2
    db.get(SourcePassage, p[6].id).excluded = True
    db.commit()
    t1 = make_topic(db, subject.id, "Instytucje", p[0:3], objectives=("Wyjaśnić instytucje",))
    t2 = make_topic(db, subject.id, "Reformy", p[3:6], objectives=("Omówić reformy",), ordinal=1)
    t3 = make_topic(db, subject.id, "Upadek", p[7:8], ordinal=2)
    tb = make_topic(db, subject.id, "Republika", pb, ordinal=3)
    s = subject.id
    q1 = cited_question(db, s, t1.id, "Jakie instytucje miały Ateny?", (p[1].id,))
    q2 = cited_question(db, s, t2.id, "Omów reformy Klejstenesa.", (p[4].id,), (p[0].id,))
    q3 = cited_question(db, s, t3.id, "Dlaczego demokracja upadła?", (p[7].id,))
    q4 = cited_question(db, s, t1.id, "Porównaj z dzisiejszą demokracją.", policy="outside")
    q5 = cited_question(
        db, s, t2.id, "Kim był Perykles?", (p[4].id,), status="needs_review", note="weryfikacja"
    )
    qb = cited_question(db, s, tb.id, "Czym była republika rzymska?", (pb[0].id,))
    return {
        "subject": s,
        "a": a.id,
        "b": b.id,
        "title": "Demokracja w Atenach",
        "p": [x.id for x in p],
        "pb": [x.id for x in pb],
        "topics": {"t1": t1.id, "t2": t2.id, "t3": t3.id, "tb": tb.id},
        "q": {"q1": q1.id, "q2": q2.id, "q3": q3.id, "q4": q4.id, "q5": q5.id, "qb": qb.id},
    }
