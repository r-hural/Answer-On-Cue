"""Builders for the backup, retention and subject-deletion tests (no tests here)."""

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from test_coverage_support import make_answer, make_objective, make_objective_question, make_round
from test_topics_support import make_subject, make_topic, make_version

from app.config import Settings
from app.features.recording.models import Recording

AUDIO_DIR = Path(__file__).resolve().parent / "fixtures" / "audio"
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def add_recording(
    session: Session,
    settings: Settings,
    *,
    purpose: str = "answer",
    age: timedelta = timedelta(0),
    wav: str = "speech_mid.wav",
    now: datetime = NOW,
) -> Recording:
    """A recording row with a real WAV file under data/recordings, created `age` before now."""
    source = AUDIO_DIR / wav
    recording = Recording(
        purpose=purpose,
        sample_rate=16000,
        duration_ms=7500,
        size_bytes=source.stat().st_size,
        stored_path="",
        vad_json='{"engine": "test"}',
        vad_engine="test",
        created_at=now - age,
    )
    session.add(recording)
    session.flush()
    recording.stored_path = f"recordings/{recording.id}.wav"
    target = settings.data_dir / recording.stored_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    session.commit()
    return recording


def subject_with_history(session: Session, settings: Settings, name: str = "Historia") -> dict:
    """One subject with a stored document, a topic and objective, two questions, a finished
    round with two answered attempts linked to real WAV files, and one mic-check recording."""
    subject = make_subject(session, name)
    version, passages = make_version(session, subject.id, count=4, excluded=())
    stored = f"documents/subject-{subject.id}/document-{version.document_id}/v1.txt"
    version.stored_path = stored
    session.commit()
    file = settings.data_dir / stored
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(b"Ateny i Sparta.")
    topic = make_topic(session, subject.id, "Ateny", passages[:2], objectives=("Wyjaśnić ustrój",))
    objective = make_objective(session, topic, "Opisać zgromadzenie", ordinal=1)
    questions = [
        make_objective_question(
            session,
            subject.id,
            topic.id,
            objective.id,
            f"Pytanie {n}?",
            passage_ids=(passages[0].id,),
        )
        for n in range(2)
    ]
    round_ = make_round(session, subject.id, at=NOW - timedelta(days=2))
    attempts = []
    recordings = []
    for question in questions:
        attempt = make_answer(
            session, round_, question, at=NOW - timedelta(days=2), band="good", factual=66.0
        )
        recording = add_recording(session, settings, age=timedelta(days=2))
        attempt.recording_id = recording.id
        session.commit()
        attempts.append(attempt)
        recordings.append(recording)
    mic = add_recording(session, settings, purpose="mic_check", age=timedelta(days=2))
    return {
        "subject": subject,
        "version": version,
        "topic": topic,
        "objective": objective,
        "questions": questions,
        "round": round_,
        "attempts": attempts,
        "recordings": recordings,
        "mic": mic,
        "document_file": file,
    }


def add_answered_round(
    session: Session, settings: Settings, history: dict, *, days_ago: int = 1
) -> dict:
    """Another finished round of the subject, one answered attempt with a real recording."""
    at = NOW - timedelta(days=days_ago)
    round_ = make_round(session, history["subject"].id, at=at)
    attempt = make_answer(
        session, round_, history["questions"][0], at=at, band="good", factual=66.0
    )
    recording = add_recording(session, settings, age=timedelta(days=days_ago))
    attempt.recording_id = recording.id
    session.commit()
    return {"round": round_, "attempt": attempt, "recording": recording}


def statements_of(call) -> list[str]:
    """Every SQL statement the call runs (for statement budgets and read-only checks)."""
    seen: list[str] = []

    def listener(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", listener)
    try:
        call()
    finally:
        event.remove(Engine, "before_cursor_execute", listener)
    return seen


def writes_in(statements: list[str]) -> list[str]:
    return [s for s in statements if s.lstrip().split(None, 1)[0].upper() in WRITE_VERBS]


WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "REPLACE")
