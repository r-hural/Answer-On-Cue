from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, event, select
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Subject(TimestampMixin, Base):
    __tablename__ = "subject"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The content language code; locked after creation. No DB-level foreign key.
    language: Mapped[str] = mapped_column(String(8), nullable=False, server_default="pl")

    documents: Mapped[list["Document"]] = relationship(
        back_populates="subject", cascade="all, delete-orphan", passive_deletes=True
    )


class Document(TimestampMixin, Base):
    __tablename__ = "document"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subject.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    # No DB-level foreign key; the before_insert listener fills the subject's default chapter.
    chapter_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)

    subject: Mapped[Subject] = relationship(back_populates="documents")
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentVersion.version",
    )


class DocumentVersion(TimestampMixin, Base):
    __tablename__ = "document_version"
    __table_args__ = (UniqueConstraint("document_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("document.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(500))
    mime: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_text: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # True when the version was stored by the replace (library.versions): its passages record
    # their predecessors, so a passage of the previous version without a successor was removed.
    # False for a first version and for versions stored before carry-over existed.
    carried_over: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    document: Mapped[Document] = relationship(back_populates="versions")
    passages: Mapped[list["SourcePassage"]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SourcePassage.ordinal",
    )


class SourcePassage(Base):
    __tablename__ = "source_passage"
    __table_args__ = (UniqueConstraint("document_version_id", "ordinal"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_version.id", ondelete="CASCADE"), index=True, nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str | None] = mapped_column(Text, nullable=True)
    locator: Mapped[str] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # No DB-level foreign key: the passage of the previous version this one replaces.
    previous_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)

    version: Mapped[DocumentVersion] = relationship(back_populates="passages")


class Chapter(TimestampMixin, Base):
    __tablename__ = "chapter"

    id: Mapped[int] = mapped_column(primary_key=True)
    # No DB-level foreign key: delete_subject removes a subject's chapters itself.
    subject_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    ordinal: Mapped[int] = mapped_column(Integer)


def default_chapter_id(connection, subject_id: int) -> int:
    """The subject's default chapter, created when missing; Core only, safe inside a flush."""
    table = Chapter.__table__
    found = connection.execute(
        select(table.c.id).where(table.c.subject_id == subject_id, table.c.is_default.is_(True))
    ).scalar()
    if found is not None:
        return found
    return connection.execute(
        table.insert().values(subject_id=subject_id, name=None, is_default=True, ordinal=0)
    ).inserted_primary_key[0]


@event.listens_for(Subject, "after_insert")
def _subject_default_chapter(_mapper, connection, target: Subject) -> None:
    default_chapter_id(connection, target.id)


@event.listens_for(Document, "before_insert")
def _document_chapter(_mapper, connection, target: Document) -> None:
    if target.chapter_id is None:
        target.chapter_id = default_chapter_id(connection, target.subject_id)
