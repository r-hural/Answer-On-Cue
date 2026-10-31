"""The monthly spending ceiling: this app's usage this month plus requests in flight.

With no ceiling (0, the default) nothing here runs: `guard_for` returns None and the round and
job checks return before reading anything.
"""

import logging
import threading
from datetime import UTC, datetime, tzinfo

from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.features.costs.prices import FREE_PROVIDERS, PRICES, Price, price_for
from app.features.costs.schemas import MonthState
from app.features.providers.models import UsageEventRow as U
from app.features.providers.types import ProviderError, ProviderName

logger = logging.getLogger(__name__)

SPENDING_LIMIT = (
    "The monthly spending ceiling is reached: {spent} of {ceiling} used this month. "
    "Raise it in Settings → Spending."
)
ROUND_OVER_BUDGET = (
    "This round needs about {low}–{high} of AI usage, but only {left} is left of your "
    "{ceiling} monthly ceiling."
)
JOB_OVER_BUDGET = (
    "This needs about {usd} of AI usage, but only {left} is left of your {ceiling} monthly "
    "ceiling. Raise the ceiling in Settings → Spending, then resume."
)
# Rounding noise must not block a call that exactly fits.
_EPSILON = 1e-9

# USD reserved by calls in flight, per database; every reserve and release takes the lock.
_lock = threading.Lock()
_reserved: dict[str, float] = {}


def money(value: float) -> str:
    """$2.40; below one cent four decimals ($0.0042), as the UI shows it."""
    value = max(value, 0.0)
    return f"${value:.4f}" if 0 < value < 0.01 else f"${value:.2f}"


def money_range(low: float, high: float) -> str:
    low_text, high_text = money(low), money(high)
    return low_text if low_text == high_text else f"{low_text}–{high_text.removeprefix('$')}"


def month_bounds(
    now: datetime | None = None, tz: tzinfo | None = None
) -> tuple[str, datetime, datetime]:
    """("YYYY-MM", start, end) of the calendar month in the zone (default: the server's)."""
    local = (now or datetime.now(UTC)).astimezone(tz)
    return month_of(local.strftime("%Y-%m"), tz)


def month_of(month: str, tz: tzinfo | None = None) -> tuple[str, datetime, datetime]:
    """The month's bounds as naive UTC, the way usage_event.started_at is stored."""
    year, number = (int(part) for part in month.split("-"))
    start = _local(datetime(year, number, 1), tz)
    end = _local(datetime(year + (number == 12), number % 12 + 1, 1), tz)
    return month, _naive_utc(start), _naive_utc(end)


def _local(naive: datetime, tz: tzinfo | None) -> datetime:
    # astimezone() on a naive value takes the server's offset on that date (DST included).
    return naive.replace(tzinfo=tz) if tz else naive.astimezone()


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


# A paid call whose answer was unusable (malformed, or cut off at max_tokens) was still billed.
BILLED_ERROR = "invalid_output"
_ROLES = ("generation", "assessment", "transcription", "voice")
_UNKNOWN_MODEL = "(unknown)"


def _usd_sql(price: Price):
    return (
        U.input_tokens * (price.input_per_m / 1_000_000)
        + U.output_tokens * (price.output_per_m / 1_000_000)
        + U.audio_seconds * (price.audio_per_min / 60)
        + U.characters * (price.chars_per_m / 1_000_000)
    )


def priced_from_usage():
    """prices.cost_of(provider, model, role, usage) as a SQL expression over one row: the
    known models, then each provider's highest price per role, then the overall fallback."""
    whens = [(U.provider.in_(FREE_PROVIDERS), literal(0.0))]
    whens.append(
        (U.provider == "azure_speech", _usd_sql(price_for("azure_speech", "", "voice")[0]))
    )
    for (provider, model), price in PRICES.items():
        whens.append((and_(U.provider == provider, U.model == model), _usd_sql(price)))
    for provider in sorted({name for name, _ in PRICES} - {"azure_speech", *FREE_PROVIDERS}):
        for role in _ROLES:
            price = price_for(provider, _UNKNOWN_MODEL, role)[0]
            whens.append((and_(U.provider == provider, U.role == role), _usd_sql(price)))
    fallback = price_for(_UNKNOWN_MODEL, _UNKNOWN_MODEL, "generation")[0]
    return case(*whens, else_=_usd_sql(fallback))


def counted():
    """The rows that cost money: successful calls and billed failures."""
    return or_(U.status == "ok", and_(U.status == "error", U.error_kind == BILLED_ERROR))


def spend_usd():
    """One row's spend, shared by the ceiling, the month box, the Usage page and round costs:
    the stored cost; a row from before 0014 (no cost) priced from its usage; a billed failure
    at its stored cost or else its pre-call estimate; anything else (blocked, other errors) 0."""
    return case(
        (U.status == "ok", func.coalesce(U.cost_usd, priced_from_usage())),
        (counted(), func.coalesce(U.cost_usd, U.estimated_usd, 0.0)),
        else_=0.0,
    )


def spent_between(session: Session, start: datetime, end: datetime) -> float:
    """One statement whatever the number of rows."""
    return session.scalar(
        select(func.coalesce(func.sum(spend_usd()), 0.0)).where(
            counted(), U.started_at >= start, U.started_at < end
        )
    )


def reserved_usd(database_url: str) -> float:
    with _lock:
        return _reserved.get(database_url, 0.0)


def month_state(
    session: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    tz: tzinfo | None = None,
    month: str | None = None,
) -> MonthState:
    """Read only, one statement whatever the number of rows."""
    if month is None:
        month, start, end = month_bounds(now, tz)
    else:
        month, start, end = month_of(month, tz)
    spent = spent_between(session, start, end)
    reserved = reserved_usd(settings.effective_database_url)
    ceiling = settings.spending_ceiling_usd
    return MonthState(
        month=month,
        ceiling_usd=ceiling,
        spent_usd=spent,
        reserved_usd=reserved,
        remaining_usd=max(ceiling - spent - reserved, 0.0) if ceiling else None,
    )


class Reservation:
    """Holds its amount as in flight until the block exits (after the call's row is written)."""

    def __init__(self, database_url: str, amount: float) -> None:
        self.database_url = database_url
        self.amount = amount

    def __enter__(self) -> "Reservation":
        return self

    def __exit__(self, *exc) -> None:
        with _lock:
            left = _reserved.get(self.database_url, 0.0) - self.amount
            if left > _EPSILON:
                _reserved[self.database_url] = left
            else:
                _reserved.pop(self.database_url, None)


class SpendingGuard:
    def __init__(
        self,
        database_url: str,
        ceiling_usd: float,
        *,
        now=None,
        tz: tzinfo | None = None,
    ) -> None:
        self.database_url = database_url
        self.ceiling_usd = ceiling_usd
        self._now = now  # a callable; tests fix the clock
        self._tz = tz

    def _spent(self) -> float:
        from app.db.session import SessionLocal

        _, start, end = month_bounds(self._now() if self._now else None, self._tz)
        with SessionLocal(self.database_url) as session:
            return spent_between(session, start, end)

    def reserve(self, estimate_usd: float, provider: ProviderName = "openai") -> Reservation:
        """Raises ProviderError("spending_limit") when this call would pass the ceiling."""
        with _lock:  # read and reserve together: a parallel call sees this one in flight
            try:
                spent = self._spent()
            except Exception as error:  # a locked database must not stop the learner
                logger.warning("The month's spend could not be read: %s", error)
                spent = 0.0
            reserved = _reserved.get(self.database_url, 0.0)
            if spent + reserved + estimate_usd > self.ceiling_usd + _EPSILON:
                raise ProviderError(
                    "spending_limit",
                    provider,
                    SPENDING_LIMIT.format(
                        spent=money(spent + reserved), ceiling=money(self.ceiling_usd)
                    ),
                )
            _reserved[self.database_url] = reserved + estimate_usd
        return Reservation(self.database_url, estimate_usd)


def guard_for(settings: Settings) -> SpendingGuard | None:
    """None without a ceiling: no read, no lock, no change to any call."""
    if not settings.spending_ceiling_usd:
        return None
    return SpendingGuard(settings.effective_database_url, settings.spending_ceiling_usd)
