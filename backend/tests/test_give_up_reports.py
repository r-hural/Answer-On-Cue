"""A given-up answer is not assessed: left out of every score, never Weak (D4)."""

import csv
import io
from datetime import UTC, datetime, timedelta

import pytest
from test_assessment_support import ai
from test_coverage_support import make_answer, make_objective, make_objective_question, make_round
from test_topics_support import make_subject, make_topic, make_version, upgrade

from app.db.session import SessionLocal
from app.features.assessment.aggregate import ItemForSummary, round_knowledge_summary
from app.features.assessment.schemas import DEFAULT_BAND_BOUNDS, AssessmentResult, Scores
from app.features.attempts.models import AttemptProcessing
from app.features.backup import export
from app.features.recording.models import Recording
from app.features.rounds.models import Attempt, Round
from app.features.rounds.schemas import RoundConfig

AT = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
GOOD_TOTAL = 66.0
STORED_FACTUAL = 41.7  # an orphaned answer's stored score, which must never surface


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def band_values(value) -> list:
    """Every non-null "band" anywhere in a JSON value."""
    found = []
    if isinstance(value, dict):
        found += [v for k, v in value.items() if k == "band" and v is not None]
        for v in value.values():
            found += band_values(v)
    elif isinstance(value, list):
        for v in value:
            found += band_values(v)
    return found


def configure(db, round_: Round, **patch) -> None:
    config = RoundConfig.model_validate_json(round_.config_json)
    round_.config_json = RoundConfig.model_validate(
        {**config.model_dump(), **patch}
    ).model_dump_json()
    db.commit()


def failed(db, attempt: Attempt) -> Attempt:
    """What mark_attempt_result leaves for a real pipeline failure."""
    attempt.outcome = "technical_failure"
    db.commit()
    return attempt


def scene(db, **config) -> dict:
    """A finished round of one subject: a Good answer and a failed one on the same objective."""
    subject = make_subject(db, "Historia")
    _, passages = make_version(db, subject.id, count=4, excluded=())
    topic = make_topic(db, subject.id, "Ateny", passages[:2], objectives=("Wyjaśnić ustrój",))
    objective = make_objective(db, topic, "Opisać zgromadzenie", ordinal=1)
    questions = [
        make_objective_question(
            db, subject.id, topic.id, objective.id, f"Pytanie {n}?", passage_ids=(passages[0].id,)
        )
        for n in range(2)
    ]
    round_ = make_round(db, subject.id, at=AT)
    if config:
        configure(db, round_, **config)
    good = make_answer(db, round_, questions[0], at=AT, band="good", factual=GOOD_TOTAL)
    broken = failed(db, make_answer(db, round_, questions[1], at=AT, processing="failed"))
    return {
        "subject": subject,
        "topic": topic,
        "questions": questions,
        "round": round_,
        "good": good,
        "failed": broken,
    }


def give_up(client, attempt_id: int) -> dict:
    response = client.post(f"/api/attempts/{attempt_id}/give-up")
    assert response.status_code == 200, response.text
    return response.json()


def round_report(client, round_id: int) -> dict:
    response = client.get(f"/api/reports/rounds/{round_id}")
    assert response.status_code == 200, response.text
    return response.json()


def item_of(report: dict, attempt_id: int) -> dict:
    return next(i for i in report["items"] if i["attempt_id"] == attempt_id)


def test_round_report_before_and_after(client, db):
    s = scene(db)
    rid, gone = s["round"].id, s["failed"].id

    before = round_report(client, rid)
    assert before["provisional"] is True
    assert (before["knowledge"]["unresolved"], before["knowledge"]["not_assessed"]) == (1, 0)

    give_up(client, gone)
    after = round_report(client, rid)

    assert after["provisional"] is False
    knowledge = after["knowledge"]
    assert knowledge["primary_score"] == GOOD_TOTAL
    assert (knowledge["assessed"], knowledge["unresolved"], knowledge["not_assessed"]) == (1, 0, 1)
    assert sum(knowledge["band_distribution"].values()) == 1
    row = item_of(after, gone)
    assert (row["status"], row["outcome"], row["band"], row["total"]) == (
        "ready",
        "given_up",
        None,
        None,
    )
    assert band_values(row) == []
    assert "weak" not in band_values(after)


def test_history_row_follows(client, db):
    s = scene(db)
    give_up(client, s["failed"].id)
    rows = client.get("/api/reports/history", params={"subject_id": s["subject"].id}).json()
    (row,) = [r for r in rows if r["round_id"] == s["round"].id]
    assert row["primary_score"] == GOOD_TOTAL
    assert row["provisional"] is False
    assert sum(row["band_distribution"].values()) == 1


def test_attempt_report_shows_nothing_assessed(client, db):
    s = scene(db, follow_ups={"enabled": True})
    give_up(client, s["failed"].id)
    response = client.get(f"/api/reports/attempts/{s['failed'].id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["points"] == [] and body["sources"] == []
    assert body["model_answer"] is None
    assert (body["result"]["outcome"], body["result"]["stage"]) == ("given_up", "not_assessed")
    assert body["follow_up"]["reason"] == "main_unresolved"
    assert band_values(body) == []


def test_answer_again_given_up(client, db):
    s = scene(db, immediate_repeat=True)
    repeat = Attempt(
        round_item_id=s["good"].round_item_id,
        attempt_kind="immediate",
        outcome="technical_failure",
        processing_status="failed",
        started_at=AT + timedelta(minutes=1),
        ended_at=AT + timedelta(minutes=1),
    )
    db.add(repeat)
    db.flush()
    db.add(
        AttemptProcessing(
            attempt_id=repeat.id,
            status="failed",
            stage="transcribing",
            failure_kind="timeout",
            failure_message="Timeout.",
        )
    )
    db.commit()

    before = round_report(client, s["round"].id)["repeats"]["knowledge"]
    assert (before["unresolved"], before["not_assessed"]) == (1, 0)
    give_up(client, repeat.id)
    after = round_report(client, s["round"].id)["repeats"]["knowledge"]
    assert (after["unresolved"], after["not_assessed"]) == (0, 1)
    assert after["primary_score"] is None


def test_trends_point(client, db):
    s = scene(db)
    give_up(client, s["failed"].id)
    body = client.get("/api/trends", params={"subject_id": s["subject"].id}).json()
    (point,) = [p for p in body["points"] if p["round_id"] == s["round"].id]
    assert (point["answers"], point["unresolved"], point["provisional"]) == (1, 0, False)
    assert point["primary"] == GOOD_TOTAL


def test_coverage_ignores_it(client, db):
    s = scene(db)
    topic = s["topic"].id
    before = client.get(f"/api/coverage/topics/{topic}").json()
    give_up(client, s["failed"].id)
    body = client.get(f"/api/coverage/topics/{topic}").json()

    entry = next(e for e in body["history"] if e["attempt_id"] == s["failed"].id)
    assert (entry["status"], entry["outcome"], entry["band"]) == ("not_assessed", "given_up", None)
    assert body["objectives"] == before["objectives"]  # the Good answer alone, as before
    assert body["status"] == before["status"]
    assert "weak" not in band_values(body["objectives"])
    assert body["status"] != "weak"


def test_retry_round_offer_leaves_it_out(client, db):
    s = scene(db, retry_round=True)
    rid = s["round"].id
    before = client.get(f"/api/rounds/{rid}/retry").json()
    assert before["unresolved"] == 1
    give_up(client, s["failed"].id)
    offer = client.get(f"/api/rounds/{rid}/retry").json()
    assert offer["unresolved"] == 0
    assert offer["count"] == 0 and offer["ordinals"] == []


def _answers_csv(db) -> list[dict]:
    files, _, _ = export.render(db, UTC)
    (name,) = [n for n in files if n.endswith("answers.csv")]
    return list(csv.DictReader(io.StringIO(files[name].decode("utf-8-sig"))))


def _markdown(db) -> str:
    files, _, _ = export.render(db, UTC)
    return "".join(data.decode("utf-8") for name, data in files.items() if name.endswith(".md"))


SCORE_CELLS = ("band", "factual", "language", "total")


def test_export_has_no_scores(client, db):
    s = scene(db)
    give_up(client, s["failed"].id)
    rows = _answers_csv(db)
    gone = next(r for r in rows if r["outcome"] == "given_up")
    assert [gone[c] for c in SCORE_CELLS] == ["", "", "", ""]
    good = next(r for r in rows if r["outcome"] == "answered")
    assert good["band"] == "good"


def test_an_orphaned_answer_keeps_its_stored_scores_out_of_the_export(client, db):
    s = scene(db)
    stored = AssessmentResult(
        status="assessed",
        scores=Scores(
            correctness=STORED_FACTUAL,
            completeness=STORED_FACTUAL,
            factual=STORED_FACTUAL,
            language=72.0,
            total=STORED_FACTUAL,
        ),
        band="needs_improvement",
        model_answer_display="auto",
        ai=ai(),
        band_bounds=DEFAULT_BAND_BOUNDS,
        factual_weights=(30, 50),
        prompt_version="test-v1",
        question_version_id=s["questions"][1].id,
        provenance=None,
        attempts_used=1,
    )
    recording = Recording(
        purpose="answer",
        sample_rate=16000,
        duration_ms=7500,
        size_bytes=10,
        stored_path="recordings/x.wav",
        vad_json="{}",
        vad_engine="test",
        created_at=AT,
    )
    db.add(recording)
    db.flush()
    attempt = db.get(Attempt, s["failed"].id)
    attempt.outcome, attempt.processing_status = "answered", "processing"
    attempt.recording_id = recording.id
    row = db.query(AttemptProcessing).filter_by(attempt_id=attempt.id).one()
    row.status, row.stage = "processing", "assessing"
    row.failure_kind = row.failure_message = None
    row.events_json = "{}"
    row.assessment_json = stored.model_dump_json()
    db.commit()

    body = give_up(client, attempt.id)
    assert band_values(body) == []

    rows = _answers_csv(db)
    gone = next(r for r in rows if r["outcome"] == "given_up")
    assert [gone[c] for c in SCORE_CELLS] == ["", "", "", ""]
    text = _markdown(db)
    assert str(STORED_FACTUAL) not in text and "41,7" not in text
    report = round_report(client, s["round"].id)
    assert report["knowledge"]["primary_score"] == GOOD_TOTAL
    assert band_values(item_of(report, attempt.id)) == []


def _assessed(total: float) -> AssessmentResult:
    return AssessmentResult(
        status="assessed",
        scores=Scores(
            correctness=total, completeness=total, factual=total, language=80, total=total
        ),
        band="good",
        model_answer_display="on_request",
        ai=ai(),
        band_bounds=DEFAULT_BAND_BOUNDS,
        factual_weights=(30, 50),
        prompt_version="assessment-v1",
        question_version_id=1,
        provenance=None,
        attempts_used=1,
    )


def test_summary_leaves_a_given_up_item_out():
    summary = round_knowledge_summary(
        [ItemForSummary(result=_assessed(80)), ItemForSummary(result=None, given_up=True)]
    )
    assert summary.primary_score == 80
    assert (summary.assessed, summary.unresolved, summary.not_assessed) == (1, 0, 1)
    assert summary.provisional is False
    assert sum(summary.band_distribution.values()) == 1

    alone = round_knowledge_summary([ItemForSummary(result=None, given_up=True)])
    assert alone.primary_score is None
    assert (alone.unresolved, alone.not_assessed, alone.provisional) == (0, 1, False)
