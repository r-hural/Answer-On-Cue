from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UsageEventRow(Base):
    """One provider call. No foreign keys: usage is real spend and outlives subject deletion."""

    __tablename__ = "usage_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(160), index=True)
    operation: Mapped[str] = mapped_column(String(40))
    task: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(8))
    error_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    audio_seconds: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    characters: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    request_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    subject_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    round_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    attempt_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    follow_up_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)  # NULL unless "ok"
