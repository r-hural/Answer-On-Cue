"""Seeds the e2e database with a subject for the topic editor and prints {"subject_id": N}.

Usage (from backend/): python ../frontend/e2e/seed_library.py "<subject name>"
The subject gets one imported document of six passages and three topics with 2, 2 and 1
learning objectives; the first topic has one question. Only the backend's test builders are
imported; nothing in backend/ is edited. The caller passes a new name each run.
"""

import json
import sys

sys.path[:0] = [".", "tests"]

from test_topics_support import make_question, make_subject, make_topic, make_version  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402


def main(name: str) -> None:
    with SessionLocal(Settings().effective_database_url) as session:
        subject = make_subject(session, name)
        _version, passages = make_version(session, subject.id, count=6, excluded=())
        solon = ("Cel S1", "Cel S2")
        first = make_topic(
            session, subject.id, "Reformy Solona", passages[:2], solon, ("sejsachteja",), 0
        )
        make_topic(
            session, subject.id, "Reformy Klejstenesa", passages[2:4], ("Cel K1", "Cel K2"), (), 1
        )
        make_topic(session, subject.id, "Ostracyzm", passages[4:], ("Cel O1",), ("ostrakon",), 2)
        make_question(session, subject.id, first.id)
        print(json.dumps({"subject_id": subject.id}))


if __name__ == "__main__":
    main(sys.argv[1])
