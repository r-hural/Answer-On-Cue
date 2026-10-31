import json

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_topics_support import make_question, make_subject, make_topic, upgrade

from app.db.base import Base
from app.db.registry import import_all_models
from app.db.session import make_engine
from app.features.questions.models import RubricVersion
from app.features.recording.models import Recording
from app.features.rounds.models import Attempt, Round, RoundEvent, RoundItem

NEW_TABLES = {"round", "round_item", "attempt", "round_event"}


@pytest.fixture
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path.as_posix()}/s.db")
    import_all_models()
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _round(session: Session):
    subject = make_subject(session)
    topic = make_topic(session, subject.id, "Ateny")
    question = make_question(session, subject.id, topic.id)
    rubric = session.query(RubricVersion).filter_by(question_version_id=question.id).one()
    row = Round(
        subject_id=subject.id,
        round_type="standard",
        status="active",
        phase="question",
        current_index=0,
        config_json=json.dumps({}),
        seed=7,
    )
    session.add(row)
    session.flush()
    item = RoundItem(
        round_id=row.id,
        ordinal=0,
        role="main",
        question_version_id=question.id,
        rubric_version_id=rubric.id,
    )
    session.add(item)
    session.flush()
    attempt = Attempt(round_item_id=item.id, attempt_kind="initial", outcome="pending")
    session.add(attempt)
    session.flush()
    session.add(
        RoundEvent(
            round_id=row.id,
            attempt_id=attempt.id,
            action="question_ready",
            from_phase="question",
            to_phase="preparation",
            status_after="active",
        )
    )
    session.commit()
    return subject, row, item, attempt, question, rubric


def test_upgrade_creates_tables(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/t.db"
    upgrade(url)
    assert NEW_TABLES <= set(inspect(create_engine(url)).get_table_names())


def test_metadata_matches_migrations(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/m.db"
    upgrade(url)
    import_all_models()
    with create_engine(url).connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_subject_delete_cascades(session):
    subject, *_ = _round(session)
    session.delete(subject)
    session.commit()
    for model in (Round, RoundItem, Attempt, RoundEvent):
        assert session.query(model).count() == 0


def test_item_ordinal_role_unique(session):
    _, row, _, _, question, rubric = _round(session)
    session.add(
        RoundItem(
            round_id=row.id,
            ordinal=0,
            role="main",
            question_version_id=question.id,
            rubric_version_id=rubric.id,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_recording_delete_nulls_attempt_link(session):
    *_, attempt, _, _ = _round(session)
    recording = Recording(
        purpose="answer",
        sample_rate=16000,
        duration_ms=1000,
        size_bytes=10,
        stored_path="recordings/a.wav",
    )
    session.add(recording)
    session.flush()
    attempt.recording_id = recording.id
    session.commit()
    session.delete(recording)
    session.commit()
    session.refresh(attempt)
    assert attempt.recording_id is None
