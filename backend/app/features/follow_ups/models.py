from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class FollowUp(TimestampMixin, Base):
    """One main question's follow-up: its generation, and the item it became once saved."""

    __tablename__ = "follow_up"

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(
        ForeignKey("round.id", ondelete="CASCADE"), index=True, nullable=False
    )
    main_item_id: Mapped[int] = mapped_column(
        ForeignKey("round_item.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    main_attempt_id: Mapped[int] = mapped_column(
        ForeignKey("attempt.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("round_item.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(12))  # generating / ready / failed / omitted
    target: Mapped[str | None] = mapped_column(String(24), nullable=True)
    tries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    failure_kind: Mapped[str | None] = mapped_column(String(30), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance_json: Mapped[str | None] = mapped_column(Text, nullable=True)
