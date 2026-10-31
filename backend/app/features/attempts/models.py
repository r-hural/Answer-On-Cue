from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AttemptProcessing(TimestampMixin, Base):
    """One answered attempt's pipeline: every stage's output is kept once it succeeds."""

    __tablename__ = "attempt_processing"

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("attempt.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(12))
    stage: Mapped[str] = mapped_column(String(20))
    events_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    assessment_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcribe_tries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    assess_tries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failure_kind: Mapped[str | None] = mapped_column(String(30), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
