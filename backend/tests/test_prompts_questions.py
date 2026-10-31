"""Question prompts take the subject's language profile."""

import json
import re

import pytest
from test_questions_support import QuestionFaker, all_good
from test_topics_support import make_subject, make_topic, make_version, upgrade

from app.db.session import SessionLocal
from app.features.languages.models import ContentLanguage
from app.features.languages.profiles import NBSP, PROFILES, SUPPORTED
from app.features.providers.fakes import FakeTextGenerator
from app.features.providers.registry import _generation_dep
from app.features.questions.models import QuestionVersion
from app.features.questions.prompts import (
    EXTENSION_POLICY_NAMES,
    OPEN_QUESTION_OPENERS,
    POLICY_NAMES,
    generate_message,
    generate_system,
    policy_line,
    policy_names,
    verify_message,
    verify_system,
)


def flat(text: str) -> str:
    """Line breaks and indents as single spaces; a no-break space stays."""
    return re.sub(r"[ \n]+", " ", text)


def test_builders_require_a_profile():
    calls = [
        lambda: generate_system("materials"),
        lambda: verify_system("materials"),
        lambda: policy_line("materials"),
        lambda: generate_message("T", None, [], [], [], [("short", "recall")]),
        lambda: verify_message([], []),
    ]
    for call in calls:
        with pytest.raises(TypeError):
            call()


def test_policy_names():
    assert policy_names(PROFILES["pl"]) == POLICY_NAMES
    assert policy_names(PROFILES["en"]) == {
        "materials": "Only my materials",
        "expanded": "Expand the topic",
        "outside": "Only expand",
    }
    for code in SUPPORTED:
        assert code in EXTENSION_POLICY_NAMES and code in OPEN_QUESTION_OPENERS


def test_french_generation_prompt():
    system = flat(generate_system("expanded", profile=PROFILES["fr"]))
    assert "for a French exam preparation app" in system
    assert "in French, phrased" in system
    openers = f"«{NBSP}Analysez…{NBSP}», «{NBSP}Expliquez…{NBSP}», «{NBSP}Comparez…{NBSP}»"
    assert openers in system
    assert f"«{NBSP}Élargir le sujet{NBSP}»" in system
    assert "Polish" not in system


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def test_generation_job_uses_the_subject_language(db, client):
    fake = FakeTextGenerator({"questions.generate": QuestionFaker(), "questions.verify": all_good})
    client.app.dependency_overrides[_generation_dep] = lambda: fake
    db.get(ContentLanguage, "en").enabled = True
    subject = make_subject(db, "History")
    subject.language = "en"
    _, passages = make_version(db, subject.id, count=6, excluded=())
    topic = make_topic(db, subject.id, "Athens", passages[:4], ("Explain democracy",), (), 0)
    db.commit()

    payload = {
        "subject_id": subject.id,
        "topic_ids": [topic.id],
        "per_topic": 1,
        "style": "open",
        "difficulty": "recall",
    }
    response = client.post("/api/questions/generate", json=payload)
    assert response.status_code == 202, response.text

    generate = next(c for c in fake.calls if c["task"] == "questions.generate")
    verify = next(c for c in fake.calls if c["task"] == "questions.verify")
    assert "in English" in generate["system"] and "in English" in verify["system"]
    message = generate["messages"][0].content
    assert "Question 1: style=" in message
    assert "Source policy: Only my materials" in message
    db.expire_all()
    version = db.query(QuestionVersion).one()
    assert json.loads(version.provenance_json)["prompt_version"] == "questions-v4"
