"""Seeds the e2e database for the content-language spec and prints one JSON line.

Usage (from backend/, with AOC_DATA_DIR set like the web server's):
  python ../frontend/e2e/seed_content_language.py subject "<name>"   -> {"subject_id": N}
  python ../frontend/e2e/seed_content_language.py bank <subject_id>  -> {"topic_id": T, "question_ids": [Q1, Q2]}
  python ../frontend/e2e/seed_content_language.py inspect <zip>      -> {"subjects": {name: language}, "languages": [code, ...]}

English is not enabled on the shared e2e server, so the English subject is seeded here instead of
created in the form. `bank` adds one topic and two English questions over the passages of the
subject's uploaded document. Only the backend's test builders are imported; nothing in backend/
is edited.
"""

import json
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path[:0] = [".", "tests"]

from sqlalchemy import select  # noqa: E402
from test_topics_support import make_question, make_subject, make_topic  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.features.languages.profiles import SUPPORTED  # noqa: E402
from app.features.library.models import Document, DocumentVersion, SourcePassage  # noqa: E402
from app.features.questions.models import RubricVersion  # noqa: E402

QUESTIONS = (
    "Explain what ostracism was and why a citizen could be sent into exile.",
    "Describe the role of the Council of Five Hundred in Athenian democracy.",
)
DOCUMENT = (
    "Athenian democracy rested on the popular assembly.\n\n"
    "Ostracism was a vote on pottery shards that sent a citizen into exile for ten years.\n\n"
    "The Council of Five Hundred prepared the business of the assembly.\n"
)  # uploaded by the spec


def english_rubric(passage_ids: list[int]) -> dict:
    return {
        "essential_points": [
            {"id": "p1", "text": "The popular assembly", "weight": 0.5, "passage_ids": passage_ids},
            {
                "id": "p2",
                "text": "The Council of Five Hundred",
                "weight": 0.5,
                "passage_ids": passage_ids,
            },
        ],
        "model_answer": "Athenian democracy rested on the popular assembly and the council.",
    }


def subject(name: str) -> dict:
    with SessionLocal(Settings().effective_database_url) as session:
        created = make_subject(session, name)
        created.language = "en"
        session.commit()
        return {"subject_id": created.id}


def bank(subject_id: int) -> dict:
    with SessionLocal(Settings().effective_database_url) as session:
        version = session.scalar(
            select(DocumentVersion)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(Document.subject_id == subject_id)
            .order_by(DocumentVersion.id.desc())
        )
        passages = (
            list(
                session.scalars(
                    select(SourcePassage)
                    .where(
                        SourcePassage.document_version_id == version.id,
                        SourcePassage.excluded.is_(False),
                    )
                    .order_by(SourcePassage.ordinal)
                )
            )
            if version is not None
            else []
        )
        if not passages:
            print("The subject has no passages: upload its document first.", file=sys.stderr)
            sys.exit(2)
        ids = [p.id for p in passages]
        topic = make_topic(
            session,
            subject_id,
            "Athenian democracy",
            passages,
            objectives=("Explain how ostracism worked.",),
            terms=("ostracism",),
        )
        question_ids = []
        for text in QUESTIONS:
            question = make_question(session, subject_id, topic.id, text, passage_ids=tuple(ids))
            rubric = session.scalar(
                select(RubricVersion).where(RubricVersion.question_version_id == question.id)
            )
            rubric.rubric_json = json.dumps(english_rubric(ids))
            question_ids.append(question.id)
        session.commit()
        return {"topic_id": topic.id, "question_ids": question_ids}


def inspect(zip_path: str) -> dict:
    """The subjects (name: language) and the language codes of the database inside a backup."""
    with tempfile.TemporaryDirectory() as folder:
        with zipfile.ZipFile(zip_path) as archive:
            target = Path(folder) / "answeroncue.db"
            target.write_bytes(archive.read("answeroncue.db"))
        connection = sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True)
        try:
            subjects = dict(connection.execute("SELECT name, language FROM subject"))
            codes = [row[0] for row in connection.execute("SELECT code FROM content_language")]
        finally:
            connection.close()
    return {"subjects": subjects, "languages": sorted(codes, key=SUPPORTED.index)}


def main(argv: list[str]) -> None:
    command, argument = argv[1], argv[2]
    if command == "subject":
        out = subject(argument)
    elif command == "bank":
        out = bank(int(argument))
    elif command == "inspect":
        out = inspect(argument)
    else:
        raise SystemExit(f"Unknown command: {command}")
    print(json.dumps(out))


if __name__ == "__main__":
    main(sys.argv)
