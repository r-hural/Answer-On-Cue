import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Annotated, Literal, Protocol, runtime_checkable

from fastapi import Depends

from app.config import Settings, get_settings
from app.features.providers.types import Usage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UsageEvent:
    operation_id: str
    task: str
    role: str
    provider: str
    model: str
    usage: Usage
    status: Literal["ok", "error", "blocked"]
    error_kind: str | None
    started_at: datetime
    duration_ms: int
    request_id: str | None = None
    estimated_usd: float | None = None  # priced before the call


@runtime_checkable
class UsageRecorder(Protocol):
    def record(self, event: UsageEvent) -> None: ...


@dataclass
class InMemoryUsageRecorder:
    """Keeps events for the process lifetime; DurableUsageRecorder also stores them."""

    events: list[UsageEvent] = field(default_factory=list)

    def record(self, event: UsageEvent) -> None:
        self.events.append(event)


@lru_cache
def get_usage_recorder() -> UsageRecorder:
    return InMemoryUsageRecorder()


@dataclass(frozen=True)
class OperationRef:
    operation: str
    attempt_id: int | None = None
    follow_up_id: int | None = None
    job_id: int | None = None


_ATTEMPT_OP = re.compile(r"attempt-(\d+):(transcribe|assess):\d+(?::r\d+)?")
_FOLLOW_UP_OP = re.compile(r"follow-up-(\d+):generate:\d+(?::r\d+)?")
_JOB_OP = re.compile(r"([a-z_]+(?:\.[a-z_]+)+):(\d+):\d+(?::[a-z]+)?")


def parse_operation(operation_id: str, task: str) -> OperationRef:
    """What an operation id says about the call; anything unknown falls back to the task."""
    if match := _ATTEMPT_OP.fullmatch(operation_id):
        return OperationRef(f"attempt.{match.group(2)}", attempt_id=int(match.group(1)))
    if match := _FOLLOW_UP_OP.fullmatch(operation_id):
        return OperationRef("follow_up.generate", follow_up_id=int(match.group(1)))
    if match := _JOB_OP.fullmatch(operation_id):
        return OperationRef(match.group(1), job_id=int(match.group(2)))
    return OperationRef(task)


def _context(session, ref: OperationRef) -> tuple[int | None, int | None]:
    """(subject_id, round_id) of the call; lazy imports keep providers free of feature models."""
    from sqlalchemy import select

    if ref.attempt_id is not None:
        from app.features.rounds.models import Attempt, Round, RoundItem

        found = session.execute(
            select(Round.subject_id, Round.id)
            .join(RoundItem, RoundItem.round_id == Round.id)
            .join(Attempt, Attempt.round_item_id == RoundItem.id)
            .where(Attempt.id == ref.attempt_id)
        ).first()
        return (found[0], found[1]) if found else (None, None)
    if ref.follow_up_id is not None:
        from app.features.follow_ups.models import FollowUp
        from app.features.rounds.models import Round

        found = session.execute(
            select(Round.subject_id, Round.id)
            .join(FollowUp, FollowUp.round_id == Round.id)
            .where(FollowUp.id == ref.follow_up_id)
        ).first()
        return (found[0], found[1]) if found else (None, None)
    if ref.job_id is not None:
        from app.features.jobs.models import AiJob

        return session.scalar(select(AiJob.subject_id).where(AiJob.id == ref.job_id)), None
    return None, None


def stored_cost(event: UsageEvent) -> float | None:
    """The cost stored with a row: None unless "ok". A paid call whose provider reported no
    usage at all (no tokens, no audio duration, no characters, e.g. a transcription response
    without `duration`) is stored at its pre-call estimate, so the ceiling still counts it."""
    from app.features.costs.prices import cost_of

    if event.status != "ok":
        return None
    usage = event.usage
    unreported = not (
        usage.input_tokens or usage.output_tokens or usage.audio_seconds or usage.characters
    )
    if unreported and event.estimated_usd:
        return event.estimated_usd
    return cost_of(event.provider, event.model, event.role, usage)


class DurableUsageRecorder:
    """Keeps every event in memory (jobs read it) and stores it as one usage_event row."""

    def __init__(self, database_url: str, memory: InMemoryUsageRecorder) -> None:
        self.database_url = database_url
        self.memory = memory

    def record(self, event: UsageEvent) -> None:
        self.memory.record(event)
        try:
            self._store(event)
        except Exception as error:  # recording must never fail the call it describes
            logger.warning("Usage event %s was not stored: %s", event.operation_id, error)

    def _store(self, event: UsageEvent) -> None:
        from app.db.session import SessionLocal
        from app.features.providers.models import UsageEventRow

        ref = parse_operation(event.operation_id, event.task)
        # A short transaction of its own; the caller has committed before each provider call.
        with SessionLocal(self.database_url) as session:
            subject_id, round_id = _context(session, ref)
            session.add(
                UsageEventRow(
                    operation_id=event.operation_id[:160],
                    operation=ref.operation[:40],
                    task=event.task[:80],
                    role=event.role,
                    provider=event.provider,
                    model=event.model[:100],
                    status=event.status,
                    error_kind=event.error_kind,
                    input_tokens=event.usage.input_tokens,
                    output_tokens=event.usage.output_tokens,
                    audio_seconds=event.usage.audio_seconds,
                    characters=event.usage.characters,
                    request_id=event.request_id,
                    started_at=event.started_at,
                    duration_ms=event.duration_ms,
                    subject_id=subject_id,
                    round_id=round_id,
                    attempt_id=ref.attempt_id,
                    follow_up_id=ref.follow_up_id,
                    job_id=ref.job_id,
                    estimated_usd=event.estimated_usd,
                    cost_usd=stored_cost(event),
                )
            )
            session.commit()


def detach_subject(session, subject_id: int) -> int:
    """Clears the links of a deleted subject's usage rows, in the caller's transaction (no
    commit): the rows, tokens and costs stay, and a reused id never inherits them."""
    from sqlalchemy import update

    from app.features.providers.models import UsageEventRow

    return session.execute(
        update(UsageEventRow)
        .where(UsageEventRow.subject_id == subject_id)
        .values(subject_id=None, round_id=None, attempt_id=None, follow_up_id=None, job_id=None)
        .execution_options(synchronize_session=False)
    ).rowcount


DETACH_ALL = (
    "UPDATE usage_event SET subject_id = NULL, round_id = NULL, attempt_id = NULL,"
    " follow_up_id = NULL, job_id = NULL"
)


def detach_all(connection) -> int:
    """Clears every row's links in a staged restore database (a sqlite3 connection): the
    restored ids may name other subjects and rounds. The caller commits."""
    return connection.execute(DETACH_ALL).rowcount


def get_durable_usage_recorder(
    settings: Annotated[Settings, Depends(get_settings)],
) -> UsageRecorder:
    """FastAPI dependency: the app database's rows, plus the process's in-memory events."""
    recorder = get_usage_recorder()
    memory = recorder if isinstance(recorder, InMemoryUsageRecorder) else InMemoryUsageRecorder()
    return DurableUsageRecorder(settings.effective_database_url, memory)
