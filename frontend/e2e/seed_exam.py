"""Seeds the e2e database with a subject "E2E exam" of three questions (short, medium, long), and
"E2E follow-up failure" with one question whose follow-up the fake generator always fails.

Run from backend/ (the web server command does), after the migrations and before uvicorn.
It only imports the backend's test builders; nothing in backend/ is edited. Safe to run twice.
"""

import sys

sys.path[:0] = [".", "tests"]

from sqlalchemy import select  # noqa: E402
from test_topics_support import make_question, make_subject, make_topic, make_version  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.features.library.models import Subject  # noqa: E402

NAME = "E2E exam"
FAILURE_NAME = "E2E follow-up failure"
FAILURE_TEXT = "Wyjaśnij, czym była eklezja w starożytnych Atenach."

# About 60, 180 and 300 characters: the three sizes of the exam question.
TEXTS = (
    "Wyjaśnij, na czym polegał ostracyzm w starożytnych Atenach.",
    "Omów, w jaki sposób Klejstenes zreformował podział obywateli Aten i dlaczego ta zmiana "
    "osłabiła wpływy dawnych rodów arystokratycznych w zgromadzeniu ludowym oraz w radzie.",
    "Przedstaw rozwój demokracji ateńskiej od reform Solona do czasów Peryklesa. W odpowiedzi "
    "uwzględnij rolę Rady Pięciuset, zgromadzenia ludowego i sądów ludowych, wyjaśnij znaczenie "
    "diet dla ubogich obywateli oraz wskaż ograniczenia ateńskiej demokracji, takie jak "
    "wykluczenie kobiet, metojków i niewolników.",
)


def seed(session, name: str, texts: tuple[str, ...]) -> None:
    """One subject with one topic over four passages; skipped when it exists."""
    if session.scalar(select(Subject).where(Subject.name == name)) is not None:
        return
    subject = make_subject(session, name)
    _version, passages = make_version(session, subject.id, count=4, excluded=())
    topic = make_topic(session, subject.id, "Demokracja ateńska", passages, terms=("ostracyzm",))
    for text in texts:
        make_question(session, subject.id, topic.id, text, passage_ids=tuple(p.id for p in passages))


def main() -> None:
    assert len(TEXTS[0]) <= 120 < len(TEXTS[1]) <= 240 < len(TEXTS[2]), [len(t) for t in TEXTS]
    with SessionLocal(Settings().effective_database_url) as session:
        seed(session, NAME, TEXTS)
        seed(session, FAILURE_NAME, (FAILURE_TEXT,))


if __name__ == "__main__":
    main()
