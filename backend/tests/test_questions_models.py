from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.registry import import_all_models
from app.db.session import make_engine
from app.features.jobs.models import AiJob
from app.features.library.models import Subject
from app.features.questions.models import QuestionFamily, QuestionVersion, RubricVersion
from app.features.topics.models import LearningObjective, Term, Topic

BACKEND = Path(__file__).resolve().parent.parent
NEW_TABLES = {
    "ai_job",
    "topic",
    "learning_objective",
    "term",
    "topic_passage",
    "question_family",
    "question_version",
    "rubric_version",
}


def upgrade(url: str) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()
    command.upgrade(cfg, "head")


@pytest.fixture
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path.as_posix()}/s.db")
    import_all_models()
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _question(family_id: int, version: int) -> QuestionVersion:
    return QuestionVersion(
        family_id=family_id,
        version=version,
        text="Omów przyczyny upadku.",
        style="open",
        difficulty="recall",
        source_policy="materials",
        secondary_topic_ids="[]",
        suggested_prep_s=30,
        suggested_answer_s=120,
        status="active",
        provenance_json="{}",
    )


def test_upgrade_creates_all_tables(tmp_path):
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
    subject = Subject(name="Historia")
    session.add(subject)
    session.flush()
    topic = Topic(subject_id=subject.id, title="T", ordinal=0, origin="ai", status="active")
    session.add(topic)
    session.flush()
    session.add(LearningObjective(topic_id=topic.id, text="Cel", ordinal=0))
    session.add(Term(subject_id=subject.id, topic_id=topic.id, term="Polis", term_key="polis"))
    family = QuestionFamily(subject_id=subject.id, primary_topic_id=topic.id)
    session.add(family)
    session.flush()
    question = _question(family.id, 1)
    session.add(question)
    session.flush()
    session.add(RubricVersion(question_version_id=question.id, rubric_json="{}"))
    session.add(
        AiJob(
            kind="topics.extract",
            subject_id=subject.id,
            status="queued",
            steps_total=1,
            steps_done=0,
            params_json="{}",
            state_json="{}",
        )
    )
    session.commit()
    session.delete(subject)
    session.commit()
    for model in (Topic, LearningObjective, Term, QuestionFamily, QuestionVersion, RubricVersion):
        assert session.query(model).count() == 0
    assert session.query(AiJob).count() == 0


def test_term_key_unique_case_insensitive(session):
    subject = Subject(name="Historia")
    session.add(subject)
    session.flush()
    session.add(Term(subject_id=subject.id, term="Polis", term_key="Polis".casefold()))
    session.commit()
    session.add(Term(subject_id=subject.id, term="POLIS", term_key="POLIS".casefold()))
    with pytest.raises(IntegrityError):
        session.commit()


def test_question_version_unique_per_family(session):
    subject = Subject(name="Historia")
    session.add(subject)
    session.flush()
    family = QuestionFamily(subject_id=subject.id)
    session.add(family)
    session.flush()
    session.add(_question(family.id, 1))
    session.commit()
    session.add(_question(family.id, 1))
    with pytest.raises(IntegrityError):
        session.commit()
