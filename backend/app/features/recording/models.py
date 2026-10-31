from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Recording(TimestampMixin, Base):
    __tablename__ = "recording"

    id: Mapped[int] = mapped_column(primary_key=True)
    purpose: Mapped[str] = mapped_column(String(20))
    mime: Mapped[str] = mapped_column(String(30), default="audio/wav")
    sample_rate: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)
    stored_path: Mapped[str] = mapped_column(String(300))
    vad_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    vad_engine: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Set when the WAV file was deleted (retention or "Delete recordings now"); the row stays.
    audio_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
