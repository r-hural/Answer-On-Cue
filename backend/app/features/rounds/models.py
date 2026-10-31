from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Round(TimestampMixin, Base):
    __tablename__ = "round"
    # At most one adaptive extension per round.
    __table_args__ = (Index("uq_round_parent_type", "parent_round_id", "round_type", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subject.id", ondelete="CASCADE"), index=True, nullable=False
    )
    round_type: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(12))
    phase: Mapped[str] = mapped_column(String(12))
    # Whether the phase belongs to the current main question or to its follow-up.
    part: Mapped[str] = mapped_column(String(10), default="main", server_default="main")
    paused_from_phase: Mapped[str | None] = mapped_column(String(12), nullable=True)
    current_index: Mapped[int] = mapped_column(Integer)
    config_json: Mapped[str] = mapped_column(Text)
    seed: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Optimistic lock: every applied action bumps it, so two parallel requests cannot both win.
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # An extension round's parent. No DB foreign key: rounds go only with their subject.
    parent_round_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class RoundItem(Base):
    __tablename__ = "round_item"
    __table_args__ = (UniqueConstraint("round_id", "ordinal", "role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(
        ForeignKey("round.id", ondelete="CASCADE"), index=True, nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(10))
    parent_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("round_item.id", ondelete="SET NULL"), nullable=True
    )
    question_version_id: Mapped[int] = mapped_column(
        ForeignKey("question_version.id", ondelete="CASCADE"), nullable=False
    )
    rubric_version_id: Mapped[int] = mapped_column(
        ForeignKey("rubric_version.id", ondelete="CASCADE"), nullable=False
    )
    # Why an adaptive round chose it: weak | unpractised | review | balanced.
    selection: Mapped[str | None] = mapped_column(String(12), nullable=True)


class Attempt(TimestampMixin, Base):
    __tablename__ = "attempt"

    id: Mapped[int] = mapped_column(primary_key=True)
    round_item_id: Mapped[int] = mapped_column(
        ForeignKey("round_item.id", ondelete="CASCADE"), index=True, nullable=False
    )
    attempt_kind: Mapped[str] = mapped_column(String(12))
    parent_attempt_id: Mapped[int | None] = mapped_column(
        ForeignKey("attempt.id", ondelete="SET NULL"), nullable=True
    )
    outcome: Mapped[str] = mapped_column(String(20))
    processing_status: Mapped[str | None] = mapped_column(String(12), nullable=True)
    start_reason: Mapped[str | None] = mapped_column(String(10), nullable=True)
    recording_id: Mapped[int | None] = mapped_column(
        ForeignKey("recording.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RoundEvent(Base):
    """One applied action; the pause/resume rows are the round's pause events."""

    __tablename__ = "round_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(
        ForeignKey("round.id", ondelete="CASCADE"), index=True, nullable=False
    )
    attempt_id: Mapped[int | None] = mapped_column(
        ForeignKey("attempt.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(20))
    from_phase: Mapped[str] = mapped_column(String(12))
    to_phase: Mapped[str] = mapped_column(String(12))
    status_after: Mapped[str] = mapped_column(String(12))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
