"""The assessment prompt takes the subject's language profile."""

import json
import re

import pytest
from test_assessment_support import question
from test_attempts_support import FakeProviders, answered_attempt, override_providers, post_answer
from test_prompts_support import make_metrics
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.assessment.prompts import assessment_system, delivery_block, user_message
from app.features.assessment.schemas import AssessmentInput
from app.features.assessment.service import assess
from app.features.attempts.models import AttemptProcessing
from app.features.languages.models import ContentLanguage
from app.features.languages.profiles import PROFILES
from app.features.providers.fakes import FakeTextGenerator

POLISH_LETTERS = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")


def flat(text: str) -> str:
    return re.sub(r"[ \n]+", " ", text)


def _inp() -> AssessmentInput:
    return AssessmentInput(question=question(), transcript="Odpowiedź.", outcome="answered")


def test_builders_require_a_profile():
    calls = [
        lambda: assessment_system("materials"),
        lambda: delivery_block(None),
        lambda: user_message(_inp()),
    ]
    for call in calls:
        with pytest.raises(TypeError):
            call()


def test_assess_requires_a_profile():
    with pytest.raises(TypeError):
        assess(_inp(), FakeTextGenerator({}), operation_id="x")


def test_german_assessment_prompt():
    system = flat(assessment_system("materials", profile=PROFILES["de"]))
    assert "a fair German oral-exam examiner" in system
    assert "Write every learner-facing text in German" in system
    assert "rewritten in formal spoken exam German" in system
    assert "„das Ding“" in system
    assert "„halt“, „quasi“, „sozusagen“" in system
    assert "„Nur meine Materialien“" in system
    assert "Polish" not in system


def test_delivery_labels_are_english():
    uk = PROFILES["uk"]
    block = delivery_block(make_metrics(interrupted=True, silence_ratio=None), profile=uk)
    assert block.startswith("<delivery>\n- preparation time:")
    assert "- pace: " in block and " words/min" in block
    assert "- answer interrupted: yes" in block
    assert "- internal silence ratio: недоступно" in block
    assert not POLISH_LETTERS.search(block)
    assert delivery_block(None, profile=uk) == "<delivery>none</delivery>"


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def _assess_through_pipeline(client, db, language: str):
    world = make_world(db)
    if language != "pl":
        db.get(ContentLanguage, language).enabled = True
        world["subject"].language = language
    db.commit()
    fake = FakeProviders()
    override_providers(client.app, fake)
    _, attempt = answered_attempt(client, world)
    post_answer(client, attempt, "speech_mid.wav")
    db.expire_all()
    row = db.query(AttemptProcessing).one()
    return fake.fake_assessor.calls, json.loads(row.assessment_json)


def test_german_answer_is_judged_in_german(client, db):
    calls, stored = _assess_through_pipeline(client, db, "de")
    system = calls[0]["system"]
    assert "formal spoken exam German" in system and "Polish" not in system
    assert stored["prompt_version"] == "assessment-v3"


def test_polish_answer_unchanged(client, db):
    calls, stored = _assess_through_pipeline(client, db, "pl")
    assert calls[0]["system"] == assessment_system("materials", profile=PROFILES["pl"])
    assert stored["prompt_version"] == "assessment-v3"
