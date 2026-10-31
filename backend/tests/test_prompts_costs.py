"""Cost estimates use the subject's characters per token wherever the subject is known."""

import pytest
from sqlalchemy import Engine, event
from test_reports_history import count_statements
from test_topics_support import make_subject, make_topic, make_version, upgrade

from app.db.session import SessionLocal
from app.features.costs import estimates
from app.features.costs.estimates import (
    UNKNOWN_LANGUAGE_CHARS_PER_TOKEN,
    estimate_job,
    job_estimate,
    round_estimate,
    text_call_usd,
    tokens_for,
    verify_usd,
    voice_usd,
)
from app.features.jobs import service as jobs
from app.features.languages.models import ContentLanguage
from app.features.topics.service import extraction_sections

GPT = ("openai", "gpt-6.1-sol", "generation")


def test_tokens_for():
    assert tokens_for(300) == 100
    assert tokens_for(10) == 4
    assert tokens_for(10, 4.0) == 3
    assert tokens_for(10, 2.5) == 4
    assert tokens_for(0, 3.5) == 0
    assert UNKNOWN_LANGUAGE_CHARS_PER_TOKEN == 3.0


def test_defaults_keep_today():
    args = (*GPT, 12345, 700)
    assert text_call_usd(*args) == text_call_usd(*args, chars_per_token=3.0)
    voice = ("openai", "gpt-realtime-2.1-mini", 240)
    assert voice_usd(*voice) == voice_usd(*voice, chars_per_token=3.0)
    assert text_call_usd(*args, chars_per_token=4.0) < text_call_usd(*args)
    assert not hasattr(estimates, "CHARS_PER_TOKEN")


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def subject_in(db, language: str, name: str):
    if language != "pl":
        db.get(ContentLanguage, language).enabled = True
    subject = make_subject(db, name)
    subject.language = language
    db.flush()
    return subject


def extraction(client, version_id: int) -> float:
    response = client.get(f"/api/costs/estimate/extraction?document_version_id={version_id}")
    assert response.status_code == 200, response.text
    return response.json()["usd"]


def generation(client, subject_id: int) -> float:
    body = {"subject_id": subject_id, "per_topic": 5}
    response = client.post("/api/costs/estimate/generation", json=body)
    assert response.status_code == 200, response.text
    return response.json()["usd"]


def test_job_estimates_follow_the_subject(client, db):
    settings = client.app.state.settings
    found = {}
    for language in ("pl", "en", "uk"):
        subject = subject_in(db, language, f"Subject {language}")
        version, passages = make_version(db, subject.id, count=30, size=900)
        topic = make_topic(db, subject.id, "Topic", passages[:4], ordinal=0)
        db.commit()
        sections = extraction_sections(db, version)
        found[language] = {
            "extraction": extraction(client, version.id),
            "generation": generation(client, subject.id),
            "extraction_params": {"document_version_id": version.id, "sections": sections},
            "generation_params": {
                "batches": [
                    {"topic_id": topic.id, "start": 0, "count": 5, "document_version_id": None}
                ],
                "document_version_id": None,
            },
        }
    pl, en, uk = found["pl"], found["en"], found["uk"]
    assert en["extraction"] == pytest.approx(
        estimate_job(
            db, settings, "topics.extract", en["extraction_params"], chars_per_token=4.0
        ).usd
    )
    assert pl["extraction"] == pytest.approx(
        estimate_job(db, settings, "topics.extract", pl["extraction_params"]).usd
    )
    assert en["generation"] == pytest.approx(
        estimate_job(
            db, settings, "questions.generate", en["generation_params"], chars_per_token=4.0
        ).usd
    )
    assert pl["generation"] == pytest.approx(
        estimate_job(db, settings, "questions.generate", pl["generation_params"]).usd
    )
    for kind in ("extraction", "generation"):
        assert en[kind] < pl[kind] < uk[kind]


def test_round_estimate_voice_part_follows_the_subject(db, settings):
    subject = subject_in(db, "en", "History")
    db.commit()
    estimate = round_estimate(
        db,
        settings,
        subject_id=subject.id,
        question_count=10,
        answer_seconds=120,
        follow_ups=False,
        voice_provider="openai",
    )
    chars = 10 * estimates.VOICE_CHARS_PER_QUESTION
    voice = next(p for p in estimate.parts if p.operation == "voice.synthesize")
    expected = voice_usd("openai", settings.openai_voice_model, chars, chars_per_token=4.0)
    assert voice.usd == pytest.approx(expected + verify_usd(settings, chars))
    assert voice_usd("openai", settings.openai_voice_model, chars) > expected


def test_job_estimate_of_a_running_job(db, settings):
    subject = subject_in(db, "en", "History")
    version, _ = make_version(db, subject.id, count=30, size=900)
    params = {"document_version_id": version.id, "sections": extraction_sections(db, version)}
    job = jobs.create_job(
        db, "topics.extract", subject.id, params, steps_total=len(params["sections"]) + 1
    )
    db.commit()
    found = job_estimate(db, settings, job).usd
    english = estimate_job(db, settings, "topics.extract", params, chars_per_token=4.0).usd
    assert found == pytest.approx(english)
    assert found < estimate_job(db, settings, "topics.extract", params).usd


def writes_of(call) -> list[str]:
    found: list[str] = []

    def listener(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            found.append(statement)

    event.listen(Engine, "before_cursor_execute", listener)
    try:
        call()
    finally:
        event.remove(Engine, "before_cursor_execute", listener)
    return found


def test_estimate_statement_budgets_unchanged(client, db):
    subject = subject_in(db, "en", "History")
    _, passages = make_version(db, subject.id, count=16, size=200, excluded=())
    small, _ = make_version(db, subject.id, count=3, size=900, excluded=())
    large, _ = make_version(db, subject.id, count=80, size=900, excluded=())
    for n in range(2):
        make_topic(db, subject.id, f"Topic {n}", passages[2 * n : 2 * n + 2], ordinal=n)
    db.commit()
    sid = subject.id
    calls = {
        "round": lambda: client.get(
            f"/api/costs/estimate/round?subject_id={sid}&question_count=10&answer_seconds=120"
            "&follow_ups=true&voice_provider=openai"
        ),
        "generation": lambda: client.post(
            "/api/costs/estimate/generation", json={"subject_id": sid, "per_topic": 7}
        ),
    }
    before = {}
    for name, call in calls.items():
        assert writes_of(call) == []
        before[name] = count_statements(client, call)
    extraction_small = count_statements(
        client, lambda: client.get(f"/api/costs/estimate/extraction?document_version_id={small.id}")
    )
    for n in range(2, 8):
        make_topic(db, subject.id, f"Topic {n}", passages[2 * n : 2 * n + 2], ordinal=n)
    db.commit()
    for name, call in calls.items():
        assert count_statements(client, call) == before[name], name

    def large_extraction():
        return client.get(f"/api/costs/estimate/extraction?document_version_id={large.id}")

    assert writes_of(large_extraction) == []
    assert count_statements(client, large_extraction) == extraction_small
