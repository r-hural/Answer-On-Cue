"""The follow-up prompt takes the subject's language profile."""

import json
import re

import pytest
from sqlalchemy import select
from test_attempts_support import (
    FakeProviders,
    answer_current,
    override_providers,
    post_answer,
    start_round,
)
from test_prompts_support import SAMPLES
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.follow_ups.models import FollowUp
from app.features.follow_ups.prompts import follow_up_system, user_message
from app.features.languages.models import ContentLanguage
from app.features.languages.profiles import PROFILES


def flat(text: str) -> str:
    return re.sub(r"[ \n]+", " ", text)


def test_builders_require_a_profile():
    s = SAMPLES
    calls = [
        lambda: follow_up_system("materials"),
        lambda: user_message(s.question.text, s.rubric, [], s.transcript, [], []),
    ]
    for call in calls:
        with pytest.raises(TypeError):
            call()


def test_italian_follow_up_prompt():
    system = flat(follow_up_system("outside", profile=PROFILES["it"]))
    assert "You are an Italian oral-exam examiner" in system
    assert "in Italian." in system
    assert "«giusto», «sbagliato»" in system
    assert "«Solo ampliamento»" in system
    assert "Polish" not in system


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def test_german_follow_up_uses_the_subject_language(client, db):
    world = make_world(db)
    db.get(ContentLanguage, "de").enabled = True
    world["subject"].language = "de"
    db.commit()
    fake = FakeProviders()
    override_providers(client.app, fake)
    rid = start_round(client, world, "practice", 2, follow_ups={"enabled": True})["id"]
    post_answer(client, answer_current(client, rid))

    (call,) = fake.fake_follow_up.calls
    assert "German" in call["system"] and "Polish" not in call["system"]
    assert call["messages"][0].content.endswith("Ask one follow-up question.")
    db.expire_all()
    row = db.scalar(select(FollowUp))
    assert row.status == "ready", row.failure_message
    assert json.loads(row.provenance_json)["prompt_version"] == "follow-up-v3"
