"""Seeds the e2e database for the adaptive round and prints {"subject_id": N}.

Usage (from backend/): python ../frontend/e2e/seed_adaptive.py "<subject name>"
The subject gets one imported document of four passages and three topics, one objective each:
  "Ostracyzm: procedura i cel": 3 questions, one Needs improvement answer (factual 45) in a
  finished practice round 1 day ago (Needs work);
  "Reformy Solona": 2 questions, no answers (Not practised);
  "Reformy Klejstenesa": 1 question, Very good in two rounds on two days (Strong).
Every question is short and recall. Only the backend's test builders are imported; nothing in
backend/ is edited. The caller passes a new name each run.
"""

import json
import sys
from datetime import UTC, datetime, timedelta

sys.path[:0] = [".", "tests"]

from test_coverage_support import (  # noqa: E402
    make_answer,
    make_objective,
    make_objective_question,
    make_round,
)
from test_topics_support import make_subject, make_topic, make_version  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.features.recording import models as _recording_models  # noqa: E402,F401  (attempt.recording_id)

TOPICS = (
    ("Ostracyzm: procedura i cel", "Opisać procedurę ostracyzmu", 3),
    ("Reformy Solona", "Opisać podział na klasy majątkowe", 2),
    ("Reformy Klejstenesa", "Wyjaśnić podział na fyle", 1),
)


def days_ago(n: int) -> datetime:
    today = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    return today - timedelta(days=n)


def main(name: str) -> None:
    with SessionLocal(Settings().effective_database_url) as session:
        subject = make_subject(session, name)
        _version, passages = make_version(session, subject.id, count=4, excluded=())
        questions = []
        for ordinal, (title, text, count) in enumerate(TOPICS):
            topic = make_topic(session, subject.id, title, passages[:2], (), (), ordinal)
            objective = make_objective(session, topic, text)
            asked = []
            for n in range(count):
                question = make_objective_question(
                    session,
                    subject.id,
                    topic.id,
                    objective.id,
                    f"Pytanie {n + 1}: {text.lower()}.",
                    passage_ids=(passages[0].id,),
                )
                question.style = "short"
                asked.append(question)
            session.commit()
            questions.append(asked)

        def answer(question, days: int, band: str, factual: float) -> None:
            at = days_ago(days)
            round_ = make_round(session, subject.id, at=at)
            make_answer(session, round_, question, at=at, band=band, factual=factual)

        answer(questions[0][0], 1, "needs_improvement", 45)
        answer(questions[2][0], 3, "very_good", 88)
        answer(questions[2][0], 2, "very_good", 90)
        print(json.dumps({"subject_id": subject.id}))


if __name__ == "__main__":
    main(sys.argv[1])
