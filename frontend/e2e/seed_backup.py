"""Seeds the e2e database for the storage and backup screens and prints
{"subject_id": N, "round_id": R, "recording_bytes": B}.

Usage (from backend/): python ../frontend/e2e/seed_backup.py "<subject name>"
The subject gets one document (its file is written under <AOC_DATA_DIR>/documents), one topic with
a learning objective, two questions and one finished round with one answered attempt whose
recording is a WAV from backend/tests/fixtures/audio copied into <AOC_DATA_DIR>/recordings.
Only the backend's test builders are imported; nothing in backend/ is edited. The caller passes a
new name each run.
"""

import json
import sys
from datetime import UTC, datetime, timedelta

sys.path[:0] = [".", "tests"]

from test_backup_support import add_recording  # noqa: E402
from test_coverage_support import make_answer, make_objective, make_objective_question, make_round  # noqa: E402
from test_topics_support import make_subject, make_topic, make_version  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402


def main(name: str) -> None:
    settings = Settings()
    with SessionLocal(settings.effective_database_url) as session:
        subject = make_subject(session, name)
        session.commit()
        version, passages = make_version(session, subject.id, count=3, excluded=())
        stored = f"documents/subject-{subject.id}/document-{version.document_id}/v1.txt"
        version.stored_path = stored
        session.commit()
        file = settings.data_dir / stored
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes("Ateny i Sparta: demokracja i oligarchia.".encode())
        topic = make_topic(session, subject.id, "Ateny", passages, objectives=("Wyjaśnić ustrój",))
        objective = make_objective(session, topic, "Opisać zgromadzenie", ordinal=1)
        questions = [
            make_objective_question(
                session, subject.id, topic.id, objective.id, text, passage_ids=(passages[0].id,)
            )
            for text in ("Czym jest ostracyzm?", "Kto wprowadził reformy Solona?")
        ]
        at = datetime.now(UTC) - timedelta(days=1)
        round_ = make_round(session, subject.id, at=at)
        attempt = make_answer(session, round_, questions[0], at=at, band="good", factual=66.0)
        recording = add_recording(session, settings, age=timedelta(days=1), now=datetime.now(UTC))
        attempt.recording_id = recording.id
        session.commit()
        print(
            json.dumps(
                {
                    "subject_id": subject.id,
                    "round_id": round_.id,
                    "recording_bytes": recording.size_bytes,
                }
            )
        )


if __name__ == "__main__":
    main(sys.argv[1])
