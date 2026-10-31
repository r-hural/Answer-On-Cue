"""Seeds the e2e database for the recovery spec and prints {"subject_id": N}.

Usage (from backend/): python ../frontend/e2e/seed_recovery.py "<subject name>"
The subject gets one imported document of four passages and one topic, "Ostracyzm: procedura i
cel", with one objective and three short recall questions (with their rubrics) and no answers.
Only the backend's test builders are imported; nothing in backend/ is edited. The caller passes
a new name each run.
"""

import json
import sys

sys.path[:0] = [".", "tests"]

from test_coverage_support import make_objective, make_objective_question  # noqa: E402
from test_topics_support import make_subject, make_topic, make_version  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.features.recording import models as _recording_models  # noqa: E402,F401  (attempt.recording_id)

TOPIC = "Ostracyzm: procedura i cel"
OBJECTIVE = "Opisać procedurę ostracyzmu"
QUESTIONS = (
    "Na czym polegała procedura ostracyzmu w Atenach?",
    "Jaki był polityczny cel ostracyzmu?",
    "Na ile lat wygnano polityka skazanego w ostracyzmie?",
)


def main(name: str) -> None:
    with SessionLocal(Settings().effective_database_url) as session:
        subject = make_subject(session, name)
        _version, passages = make_version(session, subject.id, count=4, excluded=())
        topic = make_topic(session, subject.id, TOPIC, passages[:2], (), (), 0)
        objective = make_objective(session, topic, OBJECTIVE)
        for text in QUESTIONS:
            question = make_objective_question(
                session, subject.id, topic.id, objective.id, text, passage_ids=(passages[0].id,)
            )
            question.style = "short"
        session.commit()
        print(json.dumps({"subject_id": subject.id}))


if __name__ == "__main__":
    main(sys.argv[1])
