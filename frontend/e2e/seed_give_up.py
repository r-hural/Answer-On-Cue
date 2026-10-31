"""Seeds the e2e database for giving up an answer and prints
{"subject_id", "round_id", "failed_attempt_id"}.

Usage (from backend/): python ../frontend/e2e/seed_give_up.py "<subject name>" [finished|in-round]
The subject gets two questions and one practice round: a Good answer and one whose
transcription failed, stored as the pipeline leaves a real failure (outcome technical_failure,
processing failed, a failed processing row). Mode "finished" (the default) ends the round;
"in-round" leaves it active on the failed answer's feedback, the current and last item. Only the backend's test builders are imported;
nothing in backend/ is edited. The caller passes a new name each run.
"""

import json
import sys
from datetime import UTC, datetime

sys.path[:0] = [".", "tests"]

from test_coverage_support import make_answer, make_round  # noqa: E402
from test_topics_support import make_question, make_subject, make_topic, make_version  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.features.recording import models as _recording_models  # noqa: E402,F401


def main(name: str, mode: str = "finished") -> None:
    settings = Settings()
    with SessionLocal(settings.effective_database_url) as session:
        subject = make_subject(session, name)
        _, passages = make_version(session, subject.id, count=2, excluded=())
        topic = make_topic(session, subject.id, "Ustrój Aten", passages)
        good_q = make_question(
            session, subject.id, topic.id, "Jakie zadania miała Rada Pięciuset?",
            passage_ids=(passages[0].id,),
        )  # fmt: skip
        failed_q = make_question(
            session, subject.id, topic.id, "Na czym polegał ostracyzm w Atenach?",
            passage_ids=(passages[1].id,),
        )  # fmt: skip
        at = datetime.now(UTC).replace(microsecond=0)
        if mode == "in-round":
            round_ = make_round(
                session, subject.id, at=at, status="active", phase="feedback", current_index=1
            )
        else:
            round_ = make_round(session, subject.id, at=at)
        make_answer(session, round_, good_q, at=at, band="good")
        failed = make_answer(session, round_, failed_q, at=at, processing="failed")
        failed.outcome = "technical_failure"
        session.commit()
        print(
            json.dumps(
                {
                    "subject_id": subject.id,
                    "round_id": round_.id,
                    "failed_attempt_id": failed.id,
                }
            )
        )


if __name__ == "__main__":
    main(*sys.argv[1:3])
