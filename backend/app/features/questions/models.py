from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class QuestionFamily(Base):
    __tablename__ = "question_family"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subject.id", ondelete="CASCADE"), index=True, nullable=False
    )
    primary_topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topic.id", ondelete="SET NULL"), index=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuestionVersion(TimestampMixin, Base):
    __tablename__ = "question_version"
    __table_args__ = (UniqueConstraint("family_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(
        ForeignKey("question_family.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    style: Mapped[str] = mapped_column(String(12))
    difficulty: Mapped[str] = mapped_column(String(12))
    source_policy: Mapped[str] = mapped_column(String(10))
    objective_id: Mapped[int | None] = mapped_column(
        ForeignKey("learning_objective.id", ondelete="SET NULL"), nullable=True
    )
    secondary_topic_ids: Mapped[str] = mapped_column(Text)
    suggested_prep_s: Mapped[int] = mapped_column(Integer)
    suggested_answer_s: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12))
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance_json: Mapped[str] = mapped_column(Text)


class QuestionReview(TimestampMixin, Base):
    """The user's verdict on one question version; an edit makes a new version without one."""

    __tablename__ = "question_review"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_version_id: Mapped[int] = mapped_column(
        ForeignKey("question_version.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    verdict: Mapped[str] = mapped_column(String(8))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class RubricVersion(Base):
    __tablename__ = "rubric_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_version_id: Mapped[int] = mapped_column(
        ForeignKey("question_version.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    rubric_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
