import json

import httpx2
import pytest
from test_attempts_support import FakeProviders, answered_attempt, override_providers, post_answer
from test_providers_openai import KEY, Recorder
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.attempts.models import AttemptProcessing
from app.features.languages.models import ContentLanguage
from app.features.providers.fakes import FakeTranscriber
from app.features.providers.openai_adapter import OpenAITranscriber, glossary_prompt
from app.features.providers.registry import RecordingTranscriber
from app.features.providers.types import ProviderError
from app.features.providers.usage import InMemoryUsageRecorder
from app.features.topics.models import Term

GERMAN_ANSWER = {
    "text": "Ähm, das war halt wichtig. Yyy.",
    "usage": {"type": "duration", "seconds": 3.0},
}


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def form_field(body: str, name: str) -> str | None:
    marker = f'name="{name}"\r\n\r\n'
    if marker not in body:
        return None
    return body.split(marker, 1)[1].split("\r\n", 1)[0]


def test_transcribe_requires_language():
    with pytest.raises(TypeError):
        FakeTranscriber().transcribe(b"x", "audio/wav", [], operation_id="op")  # type: ignore[call-arg]
    wrapped = RecordingTranscriber(
        lambda: FakeTranscriber(), "openai", "m", InMemoryUsageRecorder()
    )
    with pytest.raises(TypeError):
        wrapped.transcribe(b"x", "audio/wav", [], operation_id="op")  # type: ignore[call-arg]


def test_openai_transcriber_german_prefix():
    recorder = Recorder(httpx2.Response(200, json=GERMAN_ANSWER))
    transcriber = OpenAITranscriber(KEY, "gpt-transcribe", http_client=recorder.client())
    result = transcriber.transcribe(
        b"RIFF", "audio/wav", ["Westfälischer Friede"], operation_id="op-1", language="de"
    )
    body = recorder.requests[0].content.decode("utf-8")
    assert 'name="language"\r\n\r\nde\r\n' in body
    assert 'name="prompt"\r\n\r\nFachbegriffe: Westfälischer Friede\r\n' in body
    assert result.language == "de"

    transcriber.transcribe(b"RIFF", "audio/wav", ["Майдан"], operation_id="op-2", language="uk")
    body = recorder.requests[1].content.decode("utf-8")
    assert form_field(body, "prompt").startswith("Словник термінів: ")


def test_openai_transcriber_unknown_language():
    recorder = Recorder(httpx2.Response(200, json=GERMAN_ANSWER))
    transcriber = OpenAITranscriber(KEY, "gpt-transcribe", http_client=recorder.client())
    with pytest.raises(ProviderError) as caught:
        transcriber.transcribe(b"RIFF", "audio/wav", [], operation_id="op-1", language="xx")
    assert caught.value.kind == "bad_request"
    assert recorder.requests == []


def test_glossary_prompt_prefix_counts_in_limit():
    terms = [f"term{i:03d}" for i in range(200)]
    prompt = glossary_prompt(terms, "Glossary: ")
    assert prompt.startswith("Glossary: ")
    assert len(prompt) <= 800
    listed = prompt.removeprefix("Glossary: ").split(", ")
    assert listed == terms[: len(listed)]
    assert len(prompt) + len(", ") + len(terms[len(listed)]) > 800
    assert glossary_prompt([], "Glossary: ") is None


def add_terms(db, world) -> None:
    """One term per topic, so the glossary is non-empty whichever question comes first."""
    subject_id = world["subject"].id
    for topic, term in zip(world["topics"], ("Westfälischer Friede", "Reichstag"), strict=True):
        db.add(Term(subject_id=subject_id, topic_id=topic.id, term=term, term_key=term.casefold()))
    db.commit()


def test_german_subject_attempt_transcribed_in_german(client, db):
    world = make_world(db)
    db.get(ContentLanguage, "de").enabled = True
    world["subject"].language = "de"
    db.commit()
    add_terms(db, world)
    recorder = Recorder(httpx2.Response(200, json=GERMAN_ANSWER))
    fake = FakeProviders(
        transcriber=OpenAITranscriber(KEY, "gpt-transcribe", http_client=recorder.client())
    )
    override_providers(client.app, fake)

    _, attempt = answered_attempt(client, world)
    post_answer(client, attempt, "speech_mid.wav")

    [request] = recorder.requests
    body = request.content.decode("utf-8", errors="replace")
    assert form_field(body, "language") == "de"
    assert form_field(body, "prompt").startswith("Fachbegriffe: ")
    db.expire_all()
    row = db.query(AttemptProcessing).one()
    metrics = json.loads(row.metrics_json)
    assert metrics["filler_count"] == 2
    assert metrics["filler_examples"] == ["Ähm", "halt"]
    assert json.loads(row.transcript_meta_json)["language"] == "de"


def test_polish_subject_attempt_unchanged(client, db):
    world = make_world(db)
    db.commit()
    fake = FakeProviders()
    override_providers(client.app, fake)
    _, attempt = answered_attempt(client, world)
    post_answer(client, attempt, "speech_mid.wav")
    assert fake.fake_transcriber.calls[0]["language"] == "pl"
