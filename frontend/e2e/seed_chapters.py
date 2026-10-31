"""Seeds the e2e database for the chapters screens and prints
{"subject_id", "documents": {"A": id, "B": id}}.

Usage (from backend/): python ../frontend/e2e/seed_chapters.py "<subject name>"
The subject gets two imported books in its default chapter: A "Starożytna Grecja" and
B "Rewolucja francuska", four passages each. Book A has the topic "Demokracja ateńska" with
three active "materials" questions, plus one "expanded" and one "outside" question; book B has
the topic "Stany Generalne" with three active "materials" questions; the topic "Demokracja"
draws on both books (it stays in its chapter when a book moves). Only the backend's models and
test builders are imported; nothing in backend/ is edited. The caller passes a new name each run.
"""

import json
import sys

sys.path[:0] = [".", "tests"]

from sqlalchemy import select  # noqa: E402
from test_topics_support import make_question, make_subject, make_topic, make_version  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.features.questions.models import RubricVersion  # noqa: E402
from app.features.recording import models as _recording_models  # noqa: E402,F401

BOOKS = {
    "A": (
        "Starożytna Grecja",
        (
            "Demokracja ateńska opierała się na zgromadzeniu ludowym, w którym głosowali "
            "wszyscy pełnoprawni obywatele.",
            "Rada Pięciuset przygotowywała projekty ustaw dla zgromadzenia ludowego.",
            "Reformy Klejstenesa podzieliły obywateli na dziesięć fyl terytorialnych.",
            "Ostracyzm pozwalał wygnać z Aten polityka uznanego za groźnego dla demokracji.",
        ),
    ),
    "B": (
        "Rewolucja francuska",
        (
            "W maju 1789 roku król Ludwik XVI zwołał Stany Generalne do Wersalu.",
            "Stan trzeci ogłosił się Zgromadzeniem Narodowym w czerwcu 1789 roku.",
            "Zdobycie Bastylii 14 lipca 1789 roku stało się symbolem rewolucji.",
            "Deklaracja praw człowieka i obywatela głosiła równość wobec prawa.",
        ),
    ),
}
QUESTIONS = {
    "A": (
        "Omów rolę zgromadzenia ludowego w demokracji ateńskiej.",
        "Jakie zadania miała Rada Pięciuset?",
        "Na czym polegały reformy Klejstenesa?",
    ),
    "B": (
        "Dlaczego Ludwik XVI zwołał Stany Generalne?",
        "Jak powstało Zgromadzenie Narodowe?",
        "Jakie znaczenie miało zdobycie Bastylii?",
    ),
}


def main(name: str) -> None:
    with SessionLocal(Settings().effective_database_url) as session:
        subject = make_subject(session, name)
        documents = {}
        passages = {}
        for key, (title, texts) in BOOKS.items():
            version, rows = make_version(session, subject.id, count=len(texts), excluded=())
            version.document.title = title
            version.filename = f"{title}.txt"
            for row, text in zip(rows, texts, strict=True):
                row.text = text
            session.commit()
            documents[key] = version.document_id
            passages[key] = rows
        topics = {
            "A": make_topic(session, subject.id, "Demokracja ateńska", passages["A"][:3], ordinal=0),
            "B": make_topic(session, subject.id, "Stany Generalne", passages["B"][:3], ordinal=1),
        }
        make_topic(
            session, subject.id, "Demokracja", [passages["A"][3], passages["B"][3]], ordinal=2
        )
        for key, texts in QUESTIONS.items():
            for n, text in enumerate(texts):
                make_question(
                    session, subject.id, topics[key].id, text, passage_ids=(passages[key][n].id,)
                )
        for policy, text in (
            ("expanded", "Porównaj demokrację ateńską z rzymską republiką."),
            ("outside", "Jak demokracja ateńska wpłynęła na późniejsze ustroje Europy?"),
        ):
            question = make_question(session, subject.id, topics["A"].id, text)
            question.source_policy = policy
            rubric = session.scalar(
                select(RubricVersion).where(RubricVersion.question_version_id == question.id)
            )
            data = json.loads(rubric.rubric_json)
            for point in data["essential_points"]:
                point["basis"] = "expanded"  # beyond the materials, no citation
            rubric.rubric_json = json.dumps(data, ensure_ascii=False)
        session.commit()
        print(json.dumps({"subject_id": subject.id, "documents": documents}))


if __name__ == "__main__":
    main(sys.argv[1])
