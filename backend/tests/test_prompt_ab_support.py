"""A source database for the A/B tool tests, and fake generators (no tests here)."""

import hashlib
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from test_attempts_support import (
    FOLLOW_UP_TASK,
    FakeProviders,
    ai_for,
    answered_attempt,
    follow_up_for,
    override_providers,
    post_answer,
)
from test_questions_support import QuestionFaker, all_good
from test_rounds_support import make_world
from test_topics_support import BACKEND, fake_section_topics, passage_ids_in, upgrade

from app.config import REPO_ROOT, Settings
from app.db.session import SessionLocal
from app.features.assessment.service import ASSESS_TASK
from app.features.prompt_ab.loader import LoaderError, resolve_ref
from app.features.providers.fakes import FakeTextGenerator, FakeTranscriber
from app.features.questions.models import QuestionVersion

INJECTION = "<script>alert(1)</script>"


def _in_git_checkout() -> bool:
    try:
        resolve_ref("HEAD")
    except LoaderError:
        return False
    return True


# The A/B run reads the old prompts from git history: an exported tree (no .git) skips it.
needs_git = pytest.mark.skipif(not _in_git_checkout(), reason="needs a git checkout")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def database_file(settings: Settings) -> Path:
    return Path(settings.effective_database_url.removeprefix("sqlite:///"))


def sqlite_copy(source: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source)
    try:
        target = sqlite3.connect(dest)
        try:
            src.backup(target)
        finally:
            target.close()
    finally:
        src.close()
    return dest


def upgrade_to(path: Path, revision: str) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url=sqlite:///{path.as_posix()}"]})()
    command.upgrade(cfg, revision)


def build_source(client, settings: Settings, dest: Path, answers: int = 3) -> Path:
    """A Polish subject with a document, two topics with questions and `answers` assessed
    answers, built through the app in the running database, then copied to `dest`."""
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as db:
        world = make_world(db)
        first = db.get(QuestionVersion, world["questions"][0].id)
        first.text = f"Pytanie z treścią {INJECTION}?"
        db.commit()
        for n in range(answers):
            transcript = f"Odpowiedź {n} {INJECTION}" if n == 0 else f"Odpowiedź numer {n}."
            fake = FakeProviders(transcriber=FakeTranscriber(transcript))
            override_providers(client.app, fake)
            _, attempt = answered_attempt(client, world)
            post_answer(client, attempt)
    return sqlite_copy(database_file(settings), dest)


def fake_generators():
    """A factory for run(): one valid output per task, every call recorded."""
    generation = FakeTextGenerator(
        {
            "topics.extract_section": fake_section_topics,
            "questions.generate": QuestionFaker(),
            "questions.verify": all_good,
            FOLLOW_UP_TASK: lambda messages: follow_up_for(passage_ids_in(messages)),
        }
    )
    assessment = FakeTextGenerator({ASSESS_TASK: ai_for()})
    built = []

    def factory(settings):
        built.append(settings)
        return generation, assessment

    factory.generation = generation
    factory.assessment = assessment
    factory.built = built
    return factory


def disk_show(ref: str, path: str) -> str:
    """Today's files as the "old" side: only the mechanics are tested with it."""
    return (REPO_ROOT / path).read_text(encoding="utf-8")
