"""The Usage page and the month box: this app's usage per operation, round, subject and month.

Every read is one grouped statement whatever the number of rows; nothing here writes.
"""

from datetime import UTC, datetime, tzinfo

from sqlalchemy import String, case, cast, func, literal, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.features.costs.prices import price_for
from app.features.costs.schemas import Group, MonthSummary, UsageRow, UsageTable
from app.features.costs.spending import counted, month_bounds, month_of, month_state, spend_usd
from app.features.library.models import Subject
from app.features.providers.models import UsageEventRow as U

LIBRARY_OPERATIONS = ("topics.extract", "questions.generate")
VOICE_OPERATIONS = ("voice.synthesize", "voice.verify")
MONTHS_SHOWN = 12
UNLINKED = "unlinked"
VOICE = "voice"


def _local_month(tz: tzinfo | None):
    """started_at (naive UTC) as YYYY-MM in the zone; its offset now stands for every row."""
    offset = datetime.now(UTC).astimezone(tz).utcoffset()
    minutes = int(offset.total_seconds() // 60) if offset else 0
    return func.strftime("%Y-%m", U.started_at, f"{minutes:+d} minutes")


def _bounds(month: str | None, tz: tzinfo | None) -> tuple[str, datetime, datetime]:
    return month_of(month, tz) if month else month_bounds(tz=tz)


def _spent():
    """The rows' spend as the ceiling counts it (spending.spend_usd); NULL when no row cost."""
    return func.sum(case((counted(), spend_usd()), else_=None))


def _measures():
    blocked = U.status == "blocked"
    return (
        func.count(),
        func.sum(case((blocked, 1), else_=0)),
        func.sum(U.input_tokens),
        func.sum(U.output_tokens),
        func.sum(U.audio_seconds),
        func.sum(U.characters),
        func.sum(U.estimated_usd),
        _spent(),
    )


def _row(key, values, **extra) -> UsageRow:
    requests, blocked, tokens_in, tokens_out, seconds, chars, estimated, reported = values
    return UsageRow(
        key=str(key),
        requests=requests or 0,
        blocked=blocked or 0,
        input_tokens=tokens_in or 0,
        output_tokens=tokens_out or 0,
        audio_minutes=round((seconds or 0.0) / 60, 2),
        characters=chars or 0,
        estimated_usd=estimated,
        reported_usd=reported,
        **extra,
    )


def usage_table(
    session: Session, month: str | None, group: Group, *, tz: tzinfo | None = None
) -> UsageTable:
    month, start, end = _bounds(month, tz)
    in_month = (U.started_at >= start, U.started_at < end)
    measures = _measures()
    if group == "operation":
        rows = session.execute(
            select(U.operation, U.provider, U.model, U.role, *measures)
            .where(*in_month)
            .group_by(U.operation, U.provider, U.model, U.role)
            .order_by(_spent().desc().nulls_last(), U.operation)
        ).all()
        out = [
            _row(
                operation,
                values,
                provider=provider,
                model=model,
                price_known=price_for(provider, model, role)[1],
            )
            for operation, provider, model, role, *values in rows
        ]
    elif group == "round":
        rows = session.execute(
            select(U.round_id, *measures)
            .where(*in_month, U.round_id.is_not(None))
            .group_by(U.round_id)
            .order_by(U.round_id.desc())
        ).all()
        out = [_row(round_id, values) for round_id, *values in rows]
    elif group == "subject":
        key = case(
            (U.subject_id.is_not(None), cast(U.subject_id, String)),
            (U.operation.in_(VOICE_OPERATIONS), literal(VOICE)),
            else_=literal(UNLINKED),
        )
        rows = session.execute(
            select(key, Subject.name, *measures)
            .outerjoin(Subject, Subject.id == U.subject_id)
            .where(*in_month)
            .group_by(key, Subject.name)
            .order_by(Subject.name.is_(None), Subject.name, key)
        ).all()
        out = [_row(found, values, label=name) for found, name, *values in rows]
    else:
        local = _local_month(tz)
        rows = session.execute(
            select(local, *measures).group_by(local).order_by(local.desc()).limit(MONTHS_SHOWN)
        ).all()
        out = [_row(found, values) for found, *values in rows]
    return UsageTable(month=month, group=group, rows=out)


def summary(
    session: Session, settings: Settings, month: str | None = None, *, tz: tzinfo | None = None
) -> MonthSummary:
    month, start, end = _bounds(month, tz)
    state = month_state(session, settings, month=month, tz=tz)
    in_month = (U.started_at >= start, U.started_at < end)
    spend = spend_usd()
    rounds, round_usd, library_usd, voice_usd, blocked = session.execute(
        select(
            func.count(U.round_id.distinct()),
            func.sum(case((U.round_id.is_not(None), spend), else_=0.0)),
            func.sum(case((U.operation.in_(LIBRARY_OPERATIONS), spend), else_=0.0)),
            func.sum(case((U.operation.in_(VOICE_OPERATIONS), spend), else_=0.0)),
            func.sum(case((U.status == "blocked", 1), else_=0)),
        ).where(*in_month)
    ).one()
    local = _local_month(tz)
    months = list(
        session.scalars(select(local).group_by(local).order_by(local.desc()).limit(MONTHS_SHOWN))
    )
    return MonthSummary(
        month=state,
        rounds=rounds or 0,
        per_round_usd=(round_usd or 0.0) / rounds if rounds else None,
        library_usd=library_usd or 0.0,
        voice_usd=voice_usd or 0.0,
        voice_provider=settings.voice_provider,
        blocked=blocked or 0,
        months=months,
    )


def round_costs(session: Session, round_ids: list[int]) -> dict[int, float]:
    """The AI cost of each round that has usage: one statement for any number of rounds."""
    if not round_ids:
        return {}
    return dict(
        session.execute(
            select(U.round_id, func.sum(spend_usd()))
            .where(U.round_id.in_(round_ids), counted())
            .group_by(U.round_id)
        ).all()
    )
