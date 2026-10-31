"""The costs API: month summary, usage tables, estimates, prices, round cost."""

from datetime import datetime

import pytest
from sqlalchemy import Engine, event
from test_attempts_support import FakeProviders, answered_attempt, override_providers
from test_costs_support import ZONE, seed_usage
from test_reports_history import count_statements
from test_rounds_support import make_world
from test_topics_support import make_subject, make_topic, make_version, upgrade

from app.db.session import SessionLocal
from app.features.costs import estimates, usage_report
from app.features.costs.prices import PRICES, price_for, usd
from app.features.providers.types import Usage
from app.features.questions.service import topic_passages
from app.features.topics.service import compute_sections

SEP = datetime(2026, 9, 10, 12, 0, tzinfo=ZONE)
GPT = ("openai", "gpt-6.1-sol", "generation")


@pytest.fixture
def url(settings):
    upgrade(settings.effective_database_url)
    return settings.effective_database_url


@pytest.fixture
def db(url):
    with SessionLocal(url) as session:
        yield session


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


def get(client, path: str):
    response = client.get(f"/api{path}")
    assert response.status_code == 200, response.text
    return response.json()


def seed_month(url, subject_ids=(None,), rounds=(None,), per=1):
    """Known rows of October: rounds, library work and voice; one blocked; one in September."""
    for subject_id in subject_ids:
        for round_id in rounds:
            for _ in range(per):
                seed_usage(
                    url,
                    0.10,
                    operation="attempt.transcribe",
                    role="transcription",
                    model="gpt-transcribe",
                    audio_seconds=120.0,
                    estimated_usd=0.12,
                    subject_id=subject_id,
                    round_id=round_id,
                )
                seed_usage(
                    url,
                    0.20,
                    input_tokens=5000,
                    output_tokens=1000,
                    estimated_usd=0.18,
                    subject_id=subject_id,
                    round_id=round_id,
                )
    seed_usage(url, 0.30, operation="topics.extract", role="generation", estimated_usd=0.25)
    seed_usage(url, 0.05, operation="questions.generate", role="generation")
    seed_usage(
        url,
        0.016,
        operation="voice.synthesize",
        role="voice",
        provider="azure_speech",
        model="pl-PL-ZofiaNeural",
        characters=1000,
    )
    seed_usage(url, None, status="blocked", estimated_usd=0.40, round_id=rounds[0])
    seed_usage(url, 9.0, started_at=SEP)


def test_summary(client, url, db):
    seed_month(url, rounds=(11, 12))
    summary = usage_report.summary(db, client.app.state.settings, "2026-10", tz=ZONE)
    assert summary.month.month == "2026-10"
    assert summary.month.spent_usd == pytest.approx(0.6 + 0.3 + 0.05 + 0.016)
    assert (summary.month.ceiling_usd, summary.month.remaining_usd) == (0, None)
    assert summary.rounds == 2
    assert summary.per_round_usd == pytest.approx(0.30)
    assert summary.library_usd == pytest.approx(0.35)
    assert summary.voice_usd == pytest.approx(0.016)
    assert (summary.voice_provider, summary.blocked) == ("piper", 1)
    assert summary.months == ["2026-10", "2026-09"]
    client.put("/api/settings", json={"spending_ceiling_usd": 15})
    body = get(client, "/costs/summary?month=2026-10")
    assert body["month"]["ceiling_usd"] == 15
    assert body["month"]["remaining_usd"] == pytest.approx(15 - 0.966)
    assert client.get("/api/costs/summary?month=2026-13").status_code == 422


def test_usage_by_operation(client, url, db):
    seed_month(url, rounds=(11,))
    # from before 0014 (no stored cost): priced from its usage, here none
    seed_usage(url, None, operation="follow_up.generate", role="generation")
    seed_usage(url, 0.02, model="gpt-unknown")
    table = usage_report.usage_table(db, "2026-10", "operation", tz=ZONE)
    rows = {(r.key, r.model): r for r in table.rows}
    transcribe = rows[("attempt.transcribe", "gpt-transcribe")]
    assert (transcribe.requests, transcribe.blocked, transcribe.audio_minutes) == (1, 0, 2.0)
    assert transcribe.estimated_usd == pytest.approx(0.12)
    assert transcribe.reported_usd == pytest.approx(0.10)
    assess = rows[("attempt.assess", "gpt-6.1-sol")]
    assert (assess.requests, assess.blocked) == (2, 1)  # one of them blocked
    assert (assess.input_tokens, assess.output_tokens) == (5000, 1000)
    assert assess.estimated_usd == pytest.approx(0.58)
    assert assess.reported_usd == pytest.approx(0.20)
    assert (assess.price_known, assess.provider, assess.label) == (True, "openai", None)
    follow_up = rows[("follow_up.generate", "gpt-6.1-sol")]
    assert (follow_up.estimated_usd, follow_up.reported_usd) == (None, 0.0)
    assert rows[("attempt.assess", "gpt-unknown")].price_known is False
    voice = rows[("voice.synthesize", "pl-PL-ZofiaNeural")]
    assert (voice.characters, voice.provider) == (1000, "azure_speech")
    body = get(client, "/costs/usage?month=2026-10&group=operation")
    assert (body["group"], body["month"]) == ("operation", "2026-10")


def test_usage_by_round_subject_month(client, url, db):
    world = make_world(db)
    sid = world["subject"].id
    seed_month(url, subject_ids=(sid,), rounds=(5,))
    seed_usage(url, 0.07)  # detached: no subject, no round
    by_round = usage_report.usage_table(db, "2026-10", "round", tz=ZONE)
    assert [(r.key, r.requests, r.label) for r in by_round.rows] == [("5", 3, None)]
    assert by_round.rows[0].reported_usd == pytest.approx(0.30)
    keyed = {r["key"]: r for r in get(client, "/costs/usage?month=2026-10&group=subject")["rows"]}
    assert (keyed[str(sid)]["label"], keyed[str(sid)]["requests"]) == ("Historia", 2)
    assert keyed["unlinked"]["reported_usd"] == pytest.approx(0.30 + 0.05 + 0.07)
    assert keyed["unlinked"]["label"] is None
    assert keyed["voice"]["reported_usd"] == pytest.approx(0.016)
    for year in (2024, 2025):
        for month in range(1, 13):
            seed_usage(url, 0.01, started_at=datetime(year, month, 5, tzinfo=ZONE))
    months = usage_report.usage_table(db, None, "month", tz=ZONE).rows
    assert len(months) == 12
    assert [m.key for m in months[:3]] == ["2026-10", "2026-09", "2025-12"]


def test_round_estimate(client, url, db):
    world = make_world(db)
    settings = client.app.state.settings
    sid = world["subject"].id
    estimate = estimates.round_estimate(
        db, settings, subject_id=sid, question_count=10, answer_seconds=120, follow_ups=True
    )
    minute = 0.0045
    text = price_for(*GPT)[0]
    assess = usd(text, Usage(input_tokens=estimates.ASSESS_INPUT_TOKENS, output_tokens=800))
    follow = usd(text, Usage(input_tokens=estimates.FOLLOW_UP_INPUT_TOKENS, output_tokens=400))
    high = 30 * minute + 20 * assess + 10 * follow  # 10 × 2 min + 10 × 1 min, 20 answers assessed
    low = 25 * minute + 15 * assess + 5 * follow  # the budget's 25 recorded minutes
    assert estimate.high_usd == pytest.approx(high)
    assert estimate.low_usd == pytest.approx(low)
    assert 0.45 <= high <= 0.6 and estimate.low_usd < estimate.high_usd
    assert (estimate.basis, estimate.transcription_minutes, estimate.voice_provider) == (
        "typical",
        25.0,
        "piper",
    )
    assert [p.operation for p in estimate.parts] == [
        "attempt.transcribe",
        "attempt.assess",
        "follow_up.generate",
    ]
    assert estimate.fits is True and estimate.month.remaining_usd is None

    for _ in range(12):
        seed_usage(url, 0.01, input_tokens=2000, output_tokens=400)
        seed_usage(
            url,
            0.01,
            operation="attempt.transcribe",
            role="transcription",
            model="gpt-transcribe",
            audio_seconds=60.0,
        )
    measured = estimates.round_estimate(
        db, settings, subject_id=sid, question_count=10, answer_seconds=120, follow_ups=False
    )
    assert measured.basis == "measured"
    one = usd(text, Usage(input_tokens=2000, output_tokens=400))
    assert measured.high_usd == pytest.approx(10 * minute + 10 * one)
    azure = estimates.round_estimate(
        db,
        settings,
        subject_id=sid,
        question_count=10,
        answer_seconds=120,
        follow_ups=False,
        voice_provider="azure_speech",
    )
    assert azure.parts[-1].operation == "voice.synthesize"
    assert azure.parts[-1].usd == pytest.approx(10 * 200 * 16 / 1_000_000)
    client.put("/api/settings", json={"spending_ceiling_usd": 0.1})
    query = f"subject_id={sid}&question_count=10&answer_seconds=120&follow_ups=true"
    body = get(client, f"/costs/estimate/round?{query}")
    assert body["fits"] is False and body["basis"] == "measured"
    assert client.get("/api/costs/estimate/round?subject_id=999&question_count=1"
                      "&answer_seconds=60").status_code == 404  # fmt: skip


def expected_generation(db, topics, per_topic: int, version_id=None) -> float:
    total = 0.0
    for topic in topics:
        chars = sum(len(p.text) for p in topic_passages(db, topic.id, version_id))
        for start in range(0, per_topic, 5):
            count = min(5, per_topic - start)
            total += estimates.text_call_usd(*GPT, chars + estimates.SYSTEM_CHARS, count * 500)
            total += estimates.text_call_usd(
                *GPT, chars + estimates.SYSTEM_CHARS + count * estimates.QUESTION_CHARS, 600
            )
    return total


def generation(client, **body) -> dict:
    response = client.post("/api/costs/estimate/generation", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_generation_estimate(client, db):
    subject = make_subject(db)
    version, passages = make_version(db, subject.id, count=6, size=300, excluded=())
    athens = make_topic(db, subject.id, "Ateny", passages[:2], ordinal=0)
    sparta = make_topic(db, subject.id, "Sparta", passages[2:], ordinal=1)
    base = {"subject_id": subject.id, "per_topic": 7}
    estimate = generation(client, **base)
    assert (estimate["kind"], estimate["questions"], estimate["provider"]) == (
        "questions.generate",
        14,
        "openai",
    )
    assert estimate["usd"] == pytest.approx(expected_generation(db, [athens, sparta], 7))
    assert estimate["basis"] == "typical" and estimate["minutes"] >= 1
    only = generation(client, **base, topic_ids=[sparta.id])
    assert only["questions"] == 7
    assert only["usd"] == pytest.approx(expected_generation(db, [sparta], 7))
    book = generation(client, **base, document_version_id=version.id)
    assert book["usd"] == pytest.approx(expected_generation(db, [athens, sparta], 7, version.id))
    chapter = generation(client, **base, chapter_id=athens.chapter_id)
    in_chapter = [t for t in (athens, sparta) if t.chapter_id == athens.chapter_id]
    assert chapter["questions"] == 7 * len(in_chapter)
    none = generation(client, **base, topic_ids=[999])
    assert (none["questions"], none["usd"]) == (0, 0)


def test_extraction_estimate(client, db):
    subject = make_subject(db)
    version, passages = make_version(db, subject.id, count=30, size=900)
    estimate = get(client, f"/costs/estimate/extraction?document_version_id={version.id}")
    sections = compute_sections(passages)
    assert estimate["sections"] == len(sections) > 1
    chars = sum(len(p.text) for p in passages if not p.excluded)
    expected = estimates.text_call_usd(
        *GPT, chars + len(sections) * estimates.SYSTEM_CHARS, len(sections) * 1500
    ) + estimates.text_call_usd(
        *GPT, estimates.SYSTEM_CHARS + len(sections) * estimates.CONSOLIDATE_CHARS_PER_SECTION, 1500
    )
    assert estimate["usd"] == pytest.approx(expected)
    assert estimate["kind"] == "topics.extract" and estimate["minutes"] >= 1
    missing = client.get("/api/costs/estimate/extraction?document_version_id=999")
    assert missing.status_code == 404


def test_prices_endpoint(client):
    rows = get(client, "/costs/prices")
    assert len(rows) == len(PRICES)
    assert {r["checked"] for r in rows} == {"2026-10-20"}
    sonnet = next(r for r in rows if r["model"] == "claude-sonnet-5-5")
    assert (sonnet["input_per_m"], sonnet["output_per_m"]) == (2, 10)
    transcribe = next(r for r in rows if r["model"] == "gpt-transcribe")
    assert (transcribe["role"], transcribe["audio_per_min"]) == ("transcription", 0.0045)


def test_history_and_report_cost(client, db, url):
    override_providers(client.app, FakeProviders())
    world = make_world(db)
    rid, _ = answered_attempt(client, world)
    other, _ = answered_attempt(client, world)
    seed_usage(url, 0.30, round_id=rid, subject_id=world["subject"].id)
    seed_usage(url, 0.18, round_id=rid, subject_id=world["subject"].id)
    seed_usage(url, None, status="blocked", round_id=rid)
    rows = {r["round_id"]: r for r in get(client, "/reports/history")}
    assert rows[rid]["ai_cost_usd"] == pytest.approx(0.48)
    assert rows[other]["ai_cost_usd"] is None
    assert get(client, f"/reports/rounds/{rid}")["ai_cost_usd"] == pytest.approx(0.48)
    assert get(client, f"/reports/rounds/{other}")["ai_cost_usd"] is None


# ---- budgets: equal statements across data sizes, no writes ----


def budget(client, call) -> int:
    if isinstance(call, str):
        path = call
        call = lambda: client.get(f"/api{path}")  # noqa: E731
    assert writes_of(call) == []
    return count_statements(client, call)


@pytest.mark.parametrize("group", ["operation", "round", "subject", "month"])
def test_usage_budgets(client, url, db, group):
    small = make_subject(db, "Historia")
    db.commit()
    seed_month(url, subject_ids=(small.id,), rounds=(1,))
    paths = (f"/costs/usage?month=2026-10&group={group}", "/costs/summary?month=2026-10")
    before = [budget(client, p) for p in paths]
    others = [make_subject(db, f"Przedmiot {n}") for n in range(3)]
    db.commit()
    seed_month(url, subject_ids=[s.id for s in others], rounds=(2, 3, 4), per=3)
    assert [budget(client, p) for p in paths] == before


def test_estimate_budgets(client, url, db):
    world = make_world(db)
    sid = world["subject"].id
    path = f"/costs/estimate/round?subject_id={sid}&question_count=10&answer_seconds=120"
    plain = budget(client, path + "&follow_ups=true")
    for _ in range(30):
        seed_usage(url, 0.01, input_tokens=2000)
        seed_usage(url, 0.01, operation="attempt.transcribe", audio_seconds=60.0)
    assert budget(client, path + "&follow_ups=true") == plain

    subject = make_subject(db, "Biologia")
    _, passages = make_version(db, subject.id, count=16, size=200, excluded=())

    def estimate():
        generation(client, subject_id=subject.id, per_topic=7)

    for n in range(2):
        make_topic(db, subject.id, f"Temat {n}", passages[2 * n : 2 * n + 2], ordinal=n)
    two = budget(client, estimate)
    for n in range(2, 8):
        make_topic(db, subject.id, f"Temat {n}", passages[2 * n : 2 * n + 2], ordinal=n)
    assert budget(client, estimate) == two

    one, _ = make_version(db, subject.id, count=3, size=900, excluded=())
    six, _ = make_version(db, subject.id, count=80, size=900, excluded=())
    first = budget(client, f"/costs/estimate/extraction?document_version_id={one.id}")
    assert budget(client, f"/costs/estimate/extraction?document_version_id={six.id}") == first


def test_report_budgets(client, url, db):
    override_providers(client.app, FakeProviders())
    world = make_world(db)
    rounds = [answered_attempt(client, world)[0] for _ in range(5)]
    for _ in range(2):
        seed_usage(url, 0.1, round_id=rounds[0])
    report = budget(client, f"/reports/rounds/{rounds[0]}")
    one_with_usage = budget(client, "/reports/history")
    for _ in range(6):
        seed_usage(url, 0.1, round_id=rounds[0])
    assert budget(client, f"/reports/rounds/{rounds[0]}") == report
    for rid in rounds[1:]:
        seed_usage(url, 0.1, round_id=rid)
    assert budget(client, "/reports/history") == one_with_usage  # 5 rounds with usage
