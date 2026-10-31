from datetime import timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from test_attempts_audio_wait import age
from test_attempts_support import FakeProviders, answered_attempt, override_providers
from test_coverage_subject import ANCHOR, Scene, day
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.attempts.models import AttemptProcessing
from app.features.coverage.service import load_attempts
from app.features.rounds.models import Attempt, Round

WRITES = ("INSERT", "UPDATE", "DELETE")


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def statements(call) -> list[str]:
    seen = []

    def listener(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement.lstrip().upper())

    event.listen(Engine, "before_cursor_execute", listener)
    try:
        call()
    finally:
        event.remove(Engine, "before_cursor_execute", listener)
    return seen


def test_subject_coverage_query_budget(client, db):
    scene = Scene(db, (("Temat A", ("O1", "O2")), ("Temat B", ("O3", "O4"))))

    def get():
        assert client.get(f"/api/coverage/subjects/{scene.subject.id}").status_code == 200

    scene.answer("O1", day(1), "good")
    scene.answer("O2", day(1), "very_good")
    small = len(statements(get))
    for n in range(6):
        at = ANCHOR - timedelta(days=n, hours=1)
        round_ = scene.round(at)
        for text in ("O1", "O2", "O3", "O4", "O1"):
            scene.answer(text, at, "good", round_=round_)
    large = len(statements(get))
    assert small == large
    assert small <= 12, small


def reveal(db, round_id: int) -> None:
    """Finishes the round, so its answers are no longer held back for their feedback."""
    round_ = db.get(Round, round_id)
    round_.status, round_.phase = "finished", "summary"
    db.commit()


def test_subject_coverage_writes_nothing(client, db):
    override_providers(client.app, FakeProviders())
    world = make_world(db)
    round_id, attempt = answered_attempt(client, world)
    age(db, attempt, 200)
    reveal(db, round_id)
    # The overdue answer reaches the read model, so a settling read would mark it failed.
    rows = load_attempts(db, world["subject"].id)
    assert [(r.attempt_id, r.processing_status) for r in rows] == [(attempt, "processing")]
    for _ in range(2):
        seen = statements(lambda: client.get(f"/api/coverage/subjects/{world['subject'].id}"))
        assert [s for s in seen if s.startswith(WRITES)] == []
    db.expire_all()
    assert db.query(AttemptProcessing).count() == 0
    stored = db.get(Attempt, attempt)
    assert (stored.outcome, stored.processing_status) == ("answered", "processing")
