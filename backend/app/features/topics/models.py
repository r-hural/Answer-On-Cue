from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.features.library.models import default_chapter_id


class Topic(TimestampMixin, Base):
    __tablename__ = "topic"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subject.id", ondelete="CASCADE"), index=True, nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("topic.id", ondelete="CASCADE"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10))
    # No DB-level foreign key; the before_insert listener fills the subject's default chapter.
    chapter_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)


@event.listens_for(Topic, "before_insert")
def _topic_chapter(_mapper, connection, target: Topic) -> None:
    if target.chapter_id is None:
        target.chapter_id = default_chapter_id(connection, target.subject_id)


class LearningObjective(Base):
    __tablename__ = "learning_objective"

    id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topic.id", ondelete="CASCADE"), index=True, nullable=False
    )
    text: Mapped[str] = mapped_column(Text)
    ordinal: Mapped[int] = mapped_column(Integer)
    # No DB-level foreign key: the book the objective came from; NULL when shared.
    document_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)


class Term(Base):
    __tablename__ = "term"
    __table_args__ = (UniqueConstraint("subject_id", "term_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subject.id", ondelete="CASCADE"), index=True, nullable=False
    )
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topic.id", ondelete="SET NULL"), nullable=True
    )
    term: Mapped[str] = mapped_column(String(200))
    term_key: Mapped[str] = mapped_column(String(200))
    definition: Mapped[str | None] = mapped_column(Text, nullable=True)


class TopicPassage(Base):
    __tablename__ = "topic_passage"

    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topic.id", ondelete="CASCADE"), primary_key=True
    )
    passage_id: Mapped[int] = mapped_column(
        ForeignKey("source_passage.id", ondelete="CASCADE"), primary_key=True
    )
