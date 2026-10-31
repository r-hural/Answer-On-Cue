"""Seeds the e2e database for the cost screens and prints
{"subject_id": N, "round_id": R, "round_cost_usd": C}.

Usage (from backend/): python ../frontend/e2e/seed_costs.py "<subject name>"
The subject gets one document, one topic, two questions and one finished round with an answered
attempt, plus three usage_event rows of this month linked to that round (transcription,
assessment, follow-up) with known estimated and reported costs. Only the backend's test builders
and models are imported; nothing in backend/ is edited. The caller passes a new name each run.
"""

import json
import sys
from datetime import UTC, datetime, timedelta

sys.path[:0] = [".", "tests"]

from test_coverage_support import make_answer, make_objective_question, make_round  # noqa: E402
from test_topics_support import make_subject, make_topic, make_version  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.features.providers.models import UsageEventRow  # noqa: E402

# (operation, task, role, model, estimated, reported)
ROWS = (
    ("attempt.transcribe", "transcription", "transcription", "gpt-transcribe", 0.012, 0.0135),
    ("attempt.assess", "assessment.assess", "assessment", "gpt-6.1-sol", 0.05, 0.042),
    ("follow_up.generate", "follow_ups.generate", "generation", "gpt-6.1-sol", 0.03, 0.0245),
)


def main(name: str) -> None:
    settings = Settings()
    with SessionLocal(settings.effective_database_url) as session:
        subject = make_subject(session, name)
        session.commit()
        _, passages = make_version(session, subject.id, count=3, excluded=())
        topic = make_topic(session, subject.id, "Ateny", passages)
        questions = [
            make_objective_question(
                session, subject.id, topic.id, None, text, passage_ids=(passages[0].id,)
            )
            for text in ("Czym jest ostracyzm?", "Kto wprowadził reformy Solona?")
        ]
        now = datetime.now(UTC)
        round_ = make_round(session, subject.id, at=now - timedelta(minutes=30))
        attempt = make_answer(
            session, round_, questions[0], at=now - timedelta(minutes=30), band="good", factual=66.0
        )
        session.commit()
        for n, (operation, task, role, model, estimated, reported) in enumerate(ROWS):
            session.add(
                UsageEventRow(
                    operation_id=f"e2e-costs:{round_.id}:{n}",
                    operation=operation,
                    task=task,
                    role=role,
                    provider="openai",
                    model=model,
                    status="ok",
                    input_tokens=1000 if role != "transcription" else 0,
                    output_tokens=200 if role != "transcription" else 0,
                    audio_seconds=180.0 if role == "transcription" else 0.0,
                    characters=0,
                    started_at=now - timedelta(minutes=20 - n),
                    duration_ms=900,
                    subject_id=subject.id,
                    round_id=round_.id,
                    attempt_id=attempt.id if operation.startswith("attempt.") else None,
                    estimated_usd=estimated,
                    cost_usd=reported,
                )
            )
        session.commit()
        print(
            json.dumps(
                {
                    "subject_id": subject.id,
                    "round_id": round_.id,
                    "round_cost_usd": round(sum(row[5] for row in ROWS), 4),
                }
            )
        )


if __name__ == "__main__":
    main(sys.argv[1])
