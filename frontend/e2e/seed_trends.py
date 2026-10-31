"""Seeds the e2e database for the trends screen and prints
{"subject_id": N, "round_ids": [r1, r2, r3], "expanded_round_id": E}.

Usage (from backend/): python ../frontend/e2e/seed_trends.py "<subject name>"
The subject gets three finished practice rounds, 3, 2 and 1 days ago at 12:00 UTC, each with two
main short/recall answers (factual 40+50, 50+60, 70+80: means 45, 55, 75; preparation 9000/11000,
7000/9000, 5000/7000 ms). In the third round the first item was answered again (immediate, factual
100). One more finished round of expanded questions has one answer (factual 90). Only the backend's
test builders are imported; nothing in backend/ is edited. The caller passes a new name each run.
"""

import json
import sys
from datetime import UTC, datetime, timedelta

sys.path[:0] = [".", "tests"]

from test_coverage_support import make_answer, make_objective_question, make_round  # noqa: E402
from test_topics_support import make_subject  # noqa: E402
from test_trends_support import add_attempt  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.features.recording import models as _recording_models  # noqa: E402,F401  (attempt.recording_id)


def days_ago(n: int, hour: int = 12) -> datetime:
    today = datetime.now(UTC).replace(hour=hour, minute=0, second=0, microsecond=0)
    return today - timedelta(days=n)


def short_question(session, subject_id: int, text: str, policy: str = "materials"):
    question = make_objective_question(
        session, subject_id, None, None, text, source_policy=policy, difficulty="recall"
    )
    question.style = "short"  # make_question defaults to "open"
    session.commit()
    return question


def main(name: str) -> None:
    with SessionLocal(Settings().effective_database_url) as session:
        subject = make_subject(session, name)
        session.commit()
        first = short_question(session, subject.id, "Czym jest ostracyzm?")
        second = short_question(session, subject.id, "Kto wprowadził reformy Solona?")
        wide = short_question(session, subject.id, "Jak rozszerzyć temat ostracyzmu?", "expanded")

        rounds = []
        plan = [(3, (40, 50), (9000, 11000)), (2, (50, 60), (7000, 9000)), (1, (70, 80), (5000, 7000))]
        for days, (a, b), (prep_a, prep_b) in plan:
            at = days_ago(days)
            round_ = make_round(session, subject.id, at=at)
            attempt = make_answer(
                session, round_, first, at=at, band="good", factual=a, prep_ms=prep_a
            )
            make_answer(session, round_, second, at=at, band="good", factual=b, prep_ms=prep_b)
            rounds.append(round_.id)
            if days == 1:
                add_attempt(session, attempt.round_item_id, "immediate", at=at, factual=100.0)

        at = days_ago(1, hour=13)
        expanded = make_round(session, subject.id, at=at)
        config = json.loads(expanded.config_json)
        config["source_policy"] = "expanded"
        expanded.config_json = json.dumps(config)
        session.commit()
        make_answer(session, expanded, wide, at=at, band="very_good", factual=90.0)

        print(json.dumps({"subject_id": subject.id, "round_ids": rounds, "expanded_round_id": expanded.id}))


if __name__ == "__main__":
    main(sys.argv[1])
