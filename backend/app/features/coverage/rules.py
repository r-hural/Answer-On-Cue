"""Pure coverage status rules. No database, no clock: evidence in, status out."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, tzinfo
from typing import Literal

from app.features.assessment.schemas import Band

CoverageStatus = Literal["not_practised", "needs_work", "improving", "strong"]
STRONG_MIN_DAYS = 2
STRONG_MIN_ROUNDS = 2
INDEPENDENT_KINDS = ("initial", "extension")
ASSISTED_KINDS = ("immediate", "retry")
LOW_BANDS: tuple[Band, ...] = ("weak", "needs_improvement")
SUCCESS_BANDS: tuple[Band, ...] = ("good", "very_good")


@dataclass(frozen=True)
class Evidence:
    attempt_id: int
    round_id: int
    at: datetime
    band: Band
    factual: float
    independent: bool
    preparation_used_ms: int | None = None


@dataclass(frozen=True)
class StatusResult:
    status: CoverageStatus
    assisted: bool
    deciding: Evidence | None


def _aware(at: datetime) -> datetime:
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at


def local_day(at: datetime, tz: tzinfo | None = None) -> date:
    # tz=None: the server's local zone, resolved per timestamp (DST-correct).
    return _aware(at).astimezone(tz).date()


def _order(item: Evidence) -> tuple[datetime, int]:
    return (_aware(item.at), item.attempt_id)


def _is_strong(independent: list[Evidence], tz: tzinfo | None) -> bool:
    last_low = max((i for i, e in enumerate(independent) if e.band in LOW_BANDS), default=-1)
    very_good = [e for e in independent[last_low + 1 :] if e.band == "very_good"]
    rounds = {e.round_id for e in very_good}
    days = {local_day(e.at, tz) for e in very_good}
    return len(rounds) >= STRONG_MIN_ROUNDS and len(days) >= STRONG_MIN_DAYS


def status_for(evidence: list[Evidence], tz: tzinfo | None = None) -> StatusResult:
    ordered = sorted(evidence, key=_order)
    if not ordered:
        return StatusResult("not_practised", False, None)
    independent = [e for e in ordered if e.independent]
    assisted = [e for e in ordered if not e.independent]
    if not independent:
        latest = assisted[-1]
        if latest.band in SUCCESS_BANDS:
            return StatusResult("improving", True, latest)
        return StatusResult("needs_work", False, latest)
    latest = independent[-1]
    if latest.band in LOW_BANDS:
        later = [e for e in assisted if _order(e) > _order(latest) and e.band in SUCCESS_BANDS]
        if later:
            return StatusResult("improving", True, later[-1])
        return StatusResult("needs_work", False, latest)
    if _is_strong(independent, tz):
        return StatusResult("strong", False, latest)
    return StatusResult("improving", False, latest)


def topic_status(statuses: list[CoverageStatus]) -> CoverageStatus:
    if all(s == "not_practised" for s in statuses):
        return "not_practised"
    if all(s == "strong" for s in statuses):
        return "strong"
    if "needs_work" in statuses:
        return "needs_work"
    return "improving"
