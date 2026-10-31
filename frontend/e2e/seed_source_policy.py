"""Seeds the e2e database for the source-policy screens and prints
{"subject_id", "strict_round_id", "expanded_round_id", "attempt_id"}.

Usage (from backend/): python ../frontend/e2e/seed_source_policy.py "<subject name>"
The subject gets one imported document of three passages (2 and 3 contradict each other) and one
topic "Ostracyzm" with the objective "Wyjaśnić cel ostracyzmu". Questions on that objective:
  two materials questions; an expanded "mixed" one (one point beyond the materials, a model-answer
  detail beyond them); an expanded one with a contradiction issue, saved needs_review.
A finished strict practice round answers a materials question (Good 66, yesterday); a finished
expanded practice round answers the mixed question (Needs improvement 45, today) with point p1
disputed on passages 2 and 3. Only the backend's test builders are imported; nothing in backend/
is edited. The caller passes a new name each run.
"""

import json
import sys
from datetime import UTC, datetime, timedelta

sys.path[:0] = [".", "tests"]

from sqlalchemy import select  # noqa: E402
from test_coverage_support import (  # noqa: E402
    make_answer,
    make_objective,
    make_objective_question,
    make_round,
)
from test_topics_support import make_subject, make_topic, make_version  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.features.attempts.models import AttemptProcessing  # noqa: E402
from app.features.questions.models import RubricVersion  # noqa: E402
from app.features.recording import models as _recording_models  # noqa: E402,F401  (attempt.recording_id)
from app.features.rounds.schemas import RoundConfig  # noqa: E402

OBJECTIVE = "Wyjaśnić cel ostracyzmu"
PASSAGES = (
    "Ostracyzm był głosowaniem na skorupach: wskazany polityk musiał opuścić Ateny na dziesięć "
    "lat, zachowując majątek. Chronił demokrację przed tyranią.",
    "Według Arystotelesa ostracyzm wprowadził Klejstenes około 508 roku p.n.e.",
    "Ostracyzm wprowadzono dopiero w 487 roku p.n.e.; za Klejstenesa takiej procedury nie było.",
)
CONFLICT_NOTE = "Fragmenty podają różny czas wprowadzenia ostracyzmu."


def days_ago(n: int) -> datetime:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    return now - timedelta(days=n, hours=1)


def set_rubric(session, question, rubric: dict) -> None:
    row = session.scalar(
        select(RubricVersion).where(RubricVersion.question_version_id == question.id)
    )
    row.rubric_json = json.dumps(rubric, ensure_ascii=False)


def point(pid: str, text: str, weight: float, ids: list[int], basis: str = "source") -> dict:
    return {"id": pid, "text": text, "weight": weight, "passage_ids": ids, "basis": basis}


def main(name: str) -> None:
    with SessionLocal(Settings().effective_database_url) as session:
        subject = make_subject(session, name)
        _version, passages = make_version(session, subject.id, count=3, excluded=())
        for passage, text in zip(passages, PASSAGES, strict=True):
            passage.text = text
        topic = make_topic(session, subject.id, "Ostracyzm", passages, (), (), 0)
        objective = make_objective(session, topic, OBJECTIVE)
        ids = [p.id for p in passages]

        def question(text: str, policy: str, status: str = "active"):
            return make_objective_question(
                session,
                subject.id,
                topic.id,
                objective.id,
                text,
                passage_ids=(ids[0],),
                status=status,
                source_policy=policy,
            )

        strict = question("Na czym polegał ostracyzm w Atenach?", "materials")
        question("Jaki był cel ostracyzmu według podręcznika?", "materials")
        mixed = question("Wyjaśnij cel ostracyzmu i jego losy w V wieku p.n.e.", "expanded")
        set_rubric(
            session,
            mixed,
            {
                "essential_points": [
                    point("p1", "Czas wprowadzenia ostracyzmu", 0.4, [ids[1]]),
                    point("p2", "Ochrona demokracji przed tyranią", 0.3, [ids[0]], "mixed"),
                    point("p3", "Ostatni ostracyzm: Hyperbolos około 417 roku p.n.e.", 0.3, [], "expanded"),
                ],
                "model_answer": "Ostracyzm chronił demokrację przed tyranią; ostatni raz użyto go "
                "wobec Hyperbolosa około 417 roku p.n.e.",
                "beyond_material": ["Ostatni ostracyzm: Hyperbolos około 417 roku p.n.e."],
            },
        )
        doubtful = question("Kiedy wprowadzono ostracyzm?", "expanded", status="needs_review")
        doubtful.review_note = CONFLICT_NOTE
        set_rubric(
            session,
            doubtful,
            {
                "essential_points": [
                    point("p1", "Głosowanie na skorupach", 0.5, [ids[0]]),
                    point("p2", "Czas wprowadzenia", 0.5, [ids[1]]),
                ],
                "model_answer": "Źródła podają różne daty.",
                "source_issues": [
                    {"kind": "contradiction", "passage_ids": [ids[1], ids[2]], "note": CONFLICT_NOTE}
                ],
            },
        )
        session.commit()

        at = days_ago(1)
        strict_round = make_round(session, subject.id, at=at)
        make_answer(session, strict_round, strict, at=at, band="good", factual=66)

        at = days_ago(0)
        expanded_round = make_round(session, subject.id, at=at)
        config = RoundConfig.model_validate_json(expanded_round.config_json)
        expanded_round.config_json = config.model_copy(
            update={"source_policy": "expanded"}
        ).model_dump_json()
        attempt = make_answer(
            session, expanded_round, mixed, at=at, band="needs_improvement", factual=45
        )
        processing = session.scalar(
            select(AttemptProcessing).where(AttemptProcessing.attempt_id == attempt.id)
        )
        assessment = json.loads(processing.assessment_json)
        ai = assessment["ai"]
        ai["points"] = [
            {"point_id": "p1", "status": "partial", "evidence": "Klejstenes", "note": "Źródła się różnią.", "disputed": True},
            {"point_id": "p2", "status": "covered", "evidence": "przed tyranią", "note": "Dobrze.", "disputed": False},
            {"point_id": "p3", "status": "missing", "evidence": None, "note": "Brak.", "disputed": False},
        ]
        ai["source_conflicts"] = [
            {"point_id": "p1", "passage_ids": [ids[1], ids[2]], "note": CONFLICT_NOTE}
        ]
        processing.assessment_json = json.dumps(assessment, ensure_ascii=False)
        session.commit()
        print(
            json.dumps(
                {
                    "subject_id": subject.id,
                    "strict_round_id": strict_round.id,
                    "expanded_round_id": expanded_round.id,
                    "attempt_id": attempt.id,
                }
            )
        )


if __name__ == "__main__":
    main(sys.argv[1])
