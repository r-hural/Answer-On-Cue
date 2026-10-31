from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AiJob(TimestampMixin, Base):
    __tablename__ = "ai_job"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subject.id", ondelete="CASCADE"), index=True, nullable=False
    )
    document_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_version.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(12))
    steps_total: Mapped[int] = mapped_column(Integer)
    steps_done: Mapped[int] = mapped_column(Integer)
    params_json: Mapped[str] = mapped_column(Text)
    state_json: Mapped[str] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
