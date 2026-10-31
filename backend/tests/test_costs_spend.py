"""One spend rule for the ceiling, the month box, the Usage page and round costs."""

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from test_costs_support import NOW, ZONE, durable, seed_usage, usage_rows
from test_topics_support import upgrade

from app.db.session import SessionLocal
from app.features.costs import spending, usage_report
from app.features.costs.prices import PRICES, cost_of
from app.features.costs.spending import (
    SpendingGuard,
    month_of,
    priced_from_usage,
    spent_between,
)
from app.features.providers.fakes import FakeTextGenerator
from app.features.providers.models import UsageEventRow
from app.features.providers.registry import RecordingTextGenerator
from app.features.providers.types import ProviderError, Usage


class Out(BaseModel):
    x: int


@pytest.fixture
def url(settings):
    upgrade(settings.effective_database_url)
    return settings.effective_database_url


@pytest.fixture
def db(url):
    with SessionLocal(url) as session:
        yield session


@pytest.fixture(autouse=True)
def _no_reservations_left():
    yield
    spending._reserved.clear()


def october(db) -> float:
    _, start, end = month_of("2026-10", ZONE)
    return spent_between(db, start, end)


def test_billed_failures_count_at_their_estimate(url, db, monkeypatch):
    """A paid call whose answer was unusable (invalid output, truncation) was still billed."""
    seed_usage(url, None, status="error", error_kind="invalid_output", estimated_usd=0.6)
    seed_usage(url, 0.25, status="error", error_kind="invalid_output", estimated_usd=9.0)
    seed_usage(url, None, status="error", error_kind="server", estimated_usd=5.0)
    seed_usage(url, None, status="blocked", error_kind="spending_limit", estimated_usd=5.0)
    assert october(db) == pytest.approx(0.6 + 0.25)

    # through the recording adapter: a truncated answer leaves an error row the guard counts
    truncated = ProviderError("invalid_output", "openai", "The answer was cut off.")
    generator = RecordingTextGenerator(
        lambda: FakeTextGenerator({}, error=truncated),
        "openai",
        "gpt-6.1-sol",
        "generation",
        durable(url),
    )
    with pytest.raises(ProviderError):
        generator.generate_structured(
            "questions.generate", "s" * 4000, [], Out, operation_id="questions.generate:1:0"
        )
    row = usage_rows(url)[-1]
    assert (row.status, row.error_kind, row.cost_usd) == ("error", "invalid_output", None)
    monkeypatch.setattr(spending, "month_bounds", lambda now=None, tz=None: month_of("2026-10"))
    with SessionLocal(url) as session:
        session.execute(
            UsageEventRow.__table__.update()
            .where(UsageEventRow.id == row.id)
            .values(started_at=NOW.replace(tzinfo=None))
        )
        session.commit()
    guard = SpendingGuard(url, 0.85 + row.estimated_usd)
    with pytest.raises(ProviderError) as blocked:
        guard.reserve(0.001)
    assert blocked.value.kind == "spending_limit"


@pytest.mark.parametrize(
    ("provider", "model", "role"),
    [
        *[(provider, model, "generation") for provider, model in PRICES],
        ("openai", "gpt-6.1-sol", "assessment"),
        ("openai", "gpt-unknown", "assessment"),
        ("openai", "gpt-unknown", "transcription"),
        ("anthropic", "claude-unknown", "generation"),
        ("anthropic", "claude-unknown", "transcription"),
        ("azure_speech", "pl-PL-ZofiaNeural", "voice"),
        ("piper", "pl_PL-bass-high", "voice"),
        ("browser", "any", "voice"),
        ("someone", "else", "generation"),
        ("openai", "gpt-unknown", "other"),
    ],
)
def test_usage_priced_in_sql_as_in_python(url, db, provider, model, role):
    usage = Usage(input_tokens=12_345, output_tokens=678, audio_seconds=95.0, characters=4321)
    seed_usage(url, None, provider=provider, model=model, role=role, **usage.model_dump())
    priced = db.scalar(select(priced_from_usage()))
    assert priced == pytest.approx(cost_of(provider, model, role, usage))


def test_reports_show_the_spend_the_ceiling_counts(client, url, db):
    """A row from before 0014 (no stored cost) and a billed failure count everywhere alike."""
    legacy = Usage(input_tokens=50_000, output_tokens=5_000)
    legacy_usd = cost_of("openai", "gpt-6.1-sol", "assessment", legacy)
    seed_usage(url, None, round_id=7, **legacy.model_dump())
    seed_usage(
        url, None, status="error", error_kind="invalid_output", estimated_usd=0.3, round_id=7
    )
    seed_usage(url, None, status="blocked", estimated_usd=4.0, round_id=7)
    seed_usage(url, None, operation="topics.extract", role="generation", **legacy.model_dump())
    seed_usage(
        url,
        None,
        operation="voice.synthesize",
        role="voice",
        provider="azure_speech",
        model="pl-PL-ZofiaNeural",
        characters=10_000,
    )
    voice_usd = 10_000 * 16 / 1_000_000
    round_usd = legacy_usd + 0.3
    library_usd = cost_of("openai", "gpt-6.1-sol", "generation", legacy)
    total = round_usd + library_usd + voice_usd
    assert october(db) == pytest.approx(total)

    summary = usage_report.summary(db, client.app.state.settings, "2026-10", tz=ZONE)
    assert summary.month.spent_usd == pytest.approx(total)
    assert summary.per_round_usd == pytest.approx(round_usd)
    assert summary.library_usd == pytest.approx(library_usd)
    assert summary.voice_usd == pytest.approx(voice_usd)
    assert usage_report.round_costs(db, [7]) == {7: pytest.approx(round_usd)}
    for group in ("operation", "round", "subject", "month"):
        table = usage_report.usage_table(db, "2026-10", group, tz=ZONE)
        assert sum(r.reported_usd or 0 for r in table.rows if group != "round" or r.key == "7") == (
            pytest.approx(total if group != "round" else round_usd)
        ), group
