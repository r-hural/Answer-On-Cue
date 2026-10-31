from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class TranscriptReport(TimestampMixin, Base):
    """The learner's corrected text for an answer's transcript; one per attempt, stored only."""

    __tablename__ = "transcript_report"

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("attempt.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    corrected_text: Mapped[str] = mapped_column(Text)
