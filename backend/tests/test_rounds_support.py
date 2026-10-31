"""Builders for the rounds API tests (no tests here)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session
from test_topics_support import make_question, make_subject, make_topic, make_version

from app.features.recording.models import Recording

ANSWER_KEYS = ("rubric", "essential_points", "model_answer", "passages")


class Clock:
    """A frozen server clock for service.now; advance() moves it on."""

    def __init__(self) -> None:
        self.value = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **delta) -> None:
        self.value += timedelta(**delta)


def make_world(session: Session) -> dict:
    """A subject with one document, two topics and 4 active questions per topic."""
    subject = make_subject(session)
    version, passages = make_version(session, subject.id, count=4, excluded=())
    athens = make_topic(session, subject.id, "Ateny", passages[:2], ordinal=0)
    sparta = make_topic(session, subject.id, "Sparta", passages[2:], ordinal=1)
    questions = []
    for topic, cited in ((athens, passages[:2]), (sparta, passages[2:])):
        for n in range(4):
            questions.append(
                make_question(
                    session,
                    subject.id,
                    topic.id,
                    f"Pytanie {n + 1} o temacie {topic.title}.",
                    passage_ids=tuple(p.id for p in cited),
                )
            )
    return {
        "subject": subject,
        "document_id": version.document_id,
        "topics": (athens, sparta),
        "questions": questions,
    }


def make_recording(session: Session) -> Recording:
    recording = Recording(
        purpose="answer",
        sample_rate=16000,
        duration_ms=1500,
        size_bytes=48000,
        stored_path="recordings/answer.wav",
    )
    session.add(recording)
    session.commit()
    return recording


def answer_keys_in(value) -> set[str]:
    """Every answer-revealing key anywhere in a JSON value."""
    found: set[str] = set()
    if isinstance(value, dict):
        found |= {k for k in value if k in ANSWER_KEYS}
        for v in value.values():
            found |= answer_keys_in(v)
    elif isinstance(value, list):
        for v in value:
            found |= answer_keys_in(v)
    return found
