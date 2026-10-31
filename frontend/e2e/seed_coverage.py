"""Seeds the e2e database for the coverage screens and prints {"subject_id": N, "topic_id": M}.

Usage (from backend/): python ../frontend/e2e/seed_coverage.py "<subject name>"
The subject gets one imported document of four passages and two topics:
  "Ostracyzm: procedura i cel" (passages 1-2): "Opisać procedurę ostracyzmu" answered once
  (Needs improvement 51, 1 day ago) and "Wyjaśnić cel polityczny ostracyzmu" answered twice
  (Very good 88 five days ago, Very good 84 two days ago, separate rounds);
  "Reformy Solona" (passages 3-4): "Opisać podział na klasy majątkowe" (Good 66, 3 days ago) and
  "Ocenić znaczenie reform" (a question, no answers).
Every round is a finished practice round, at 12:00 UTC on its day. Only the backend's test builders
are imported; nothing in backend/ is edited. The caller passes a new name each run.
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


def days_ago(n: int) -> datetime:
    today = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    return today - timedelta(days=n)


def main(name: str) -> None:
    with SessionLocal(Settings().effective_database_url) as session:
        subject = make_subject(session, name)
        _version, passages = make_version(session, subject.id, count=4, excluded=())
        ostracism = make_topic(
            session, subject.id, "Ostracyzm: procedura i cel", passages[:2], (), ("ostrakon",), 0
        )
        solon = make_topic(session, subject.id, "Reformy Solona", passages[2:], (), (), 1)
        questions = {}
        for topic, texts in (
            (ostracism, ("Opisać procedurę ostracyzmu", "Wyjaśnić cel polityczny ostracyzmu")),
            (solon, ("Opisać podział na klasy majątkowe", "Ocenić znaczenie reform")),
        ):
            for ordinal, text in enumerate(texts):
                objective = make_objective(session, topic, text, ordinal)
                questions[text] = make_objective_question(
                    session,
                    subject.id,
                    topic.id,
                    objective.id,
                    f"Wyjaśnij: {text.lower()}.",
                    passage_ids=(passages[0].id,),
                )

        def answer(text: str, days: int, band: str, factual: float) -> None:
            at = days_ago(days)
            round_ = make_round(session, subject.id, at=at)
            make_answer(session, round_, questions[text], at=at, band=band, factual=factual)

        answer("Opisać procedurę ostracyzmu", 1, "needs_improvement", 51)
        answer("Wyjaśnić cel polityczny ostracyzmu", 5, "very_good", 88)
        answer("Wyjaśnić cel polityczny ostracyzmu", 2, "very_good", 84)
        answer("Opisać podział na klasy majątkowe", 3, "good", 66)
        print(json.dumps({"subject_id": subject.id, "topic_id": ostracism.id}))


if __name__ == "__main__":
    main(sys.argv[1])
