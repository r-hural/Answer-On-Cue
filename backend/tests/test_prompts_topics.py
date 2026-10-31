"""Topic prompts take the subject's language profile."""

import re

import pytest
from test_topics_support import (
    fake_consolidation,
    fake_section_topics,
    make_subject,
    make_version,
    upgrade,
)

from app.db.session import SessionLocal
from app.features.jobs import service as jobs
from app.features.jobs.models import AiJob
from app.features.languages.models import ContentLanguage
from app.features.languages.profiles import PROFILES
from app.features.providers.fakes import FakeTextGenerator
from app.features.providers.registry import _generation_dep
from app.features.topics.prompts import (
    article,
    consolidate_chapter_message,
    consolidate_chapter_system,
    consolidate_message,
    consolidate_system,
    extract_section_message,
    extract_section_system,
)


def flat(text: str) -> str:
    """Line breaks and indents as single spaces; a no-break space stays."""
    return re.sub(r"[ \n]+", " ", text)


def test_builders_require_a_profile():
    calls = [
        lambda: extract_section_system(),
        lambda: consolidate_system(),
        lambda: consolidate_chapter_system(),
        lambda: extract_section_message([(1, "a")]),
        lambda: consolidate_message([("s0t0", "a", "b")]),
        lambda: consolidate_chapter_message([("s0t0", "a", "b")], [("e1", "c", "d")]),
    ]
    for call in calls:
        with pytest.raises(TypeError):
            call()


def test_german_extraction_prompt():
    system = flat(extract_section_system(profile=PROFILES["de"]))
    assert "for a German oral-exam preparation app" in system
    assert "a short German topic title" in system
    assert "in German, each starting with a verb" in system
    assert "„Erklären…“, „Erläutern…“, „Vergleichen…“" in system
    assert "All user-facing text must be in German." in system
    assert "Polish" not in system


def test_article():
    assert article("English") == "an"
    assert article("Italian") == "an"
    assert article("Ukrainian") == "a"
    assert article("Polish") == "a"


def test_consolidation_prompts_name_the_language():
    es = PROFILES["es"]
    for system in (consolidate_system(profile=es), consolidate_chapter_system(profile=es)):
        text = flat(system)
        assert "for a Spanish oral-exam preparation app" in text
        assert "a Spanish title" in text
        assert "All user-facing text must be in Spanish." in text
        assert "Polish" not in text
    message = consolidate_chapter_message(
        [("s0t1", "Tema", "Descripción")], [("e12", "Otro", "Más")], profile=es
    )
    assert "Existing topics of the chapter:" in message
    assert "[s0t1] Tema: Descripción" in message and "[e12] Otro: Más" in message


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def test_extraction_job_uses_the_subject_language(db, client):
    fake = FakeTextGenerator(
        {"topics.extract_section": fake_section_topics, "topics.consolidate": fake_consolidation}
    )
    client.app.dependency_overrides[_generation_dep] = lambda: fake
    db.get(ContentLanguage, "de").enabled = True
    subject = make_subject(db, "Geschichte")
    subject.language = "de"
    version, _ = make_version(db, subject.id)
    db.commit()

    response = client.post("/api/topics/extract", json={"document_version_id": version.id})
    assert response.status_code == 202, response.text

    tasks = [c["task"] for c in fake.calls]
    assert "topics.extract_section" in tasks and "topics.consolidate" in tasks
    for call in fake.calls:
        assert "German" in call["system"] and "Polish" not in call["system"]
    for call in fake.calls:
        if call["task"] == "topics.extract_section":
            assert call["messages"][0].content.startswith("Document passages:")
    db.expire_all()
    job = db.query(AiJob).one()
    sections = [r for r in jobs.step_results(job).values() if r.get("topics")]
    assert sections and all(r["prompt_version"] == "topics-v3" for r in sections)
