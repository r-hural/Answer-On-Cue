"""Chapters: Subject > Chapter > documents. Topics merge only within a chapter.

Every subject has exactly one default chapter (`is_default`), created by the migration and by
the Subject listener in library/models.py; write paths call ensure_default_chapter so a subject
inserted by raw SQL heals. GET paths never create one.
"""

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.features.chapters.schemas import ChapterOut, MoveDocumentIn, MoveResult
from app.features.jobs import service as jobs
from app.features.library.models import (
    Chapter,
    Document,
    DocumentVersion,
    SourcePassage,
    Subject,
    default_chapter_id,
)
from app.features.questions.models import QuestionFamily, QuestionVersion
from app.features.topics.models import Topic, TopicPassage

NAME_LIMIT = 200
COUNTED_QUESTION_STATUSES = ("active", "needs_review")
WORK_KINDS = ("topics.extract", "questions.generate")  # moves wait for these

SUBJECT_NOT_FOUND = "Subject not found."
CHAPTER_NOT_FOUND = "Chapter not found."
DUPLICATE_CHAPTER = "This subject already has a chapter with this name."
INVALID_CHAPTER_NAME = "The chapter name must be 1 to 200 characters long."
DEFAULT_CHAPTER = "The first chapter of a subject cannot be deleted."
CHAPTER_NOT_EMPTY = "Move this chapter's documents and topics first."
MOVE_TARGET = "Choose a chapter or enter a new chapter name."
JOBS_RUNNING = "Wait for the topic or question work of this subject to finish first."
FOREIGN_CHAPTER = "This chapter belongs to another subject."
DOCUMENT_NOT_FOUND = "Document not found."


def ensure_default_chapter(session: Session, subject_id: int) -> int:
    session.flush()
    return default_chapter_id(session.connection(), subject_id)


def _subject(session: Session, subject_id: int) -> Subject:
    subject = session.get(Subject, subject_id)
    if subject is None:
        raise HTTPException(404, SUBJECT_NOT_FOUND)
    return subject


def get_chapter(session: Session, chapter_id: int) -> Chapter:
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(404, CHAPTER_NOT_FOUND)
    return chapter


def subject_chapter(session: Session, subject_id: int, chapter_id: int) -> Chapter:
    """The chapter, which must belong to the subject (422 FOREIGN_CHAPTER otherwise)."""
    chapter = get_chapter(session, chapter_id)
    if chapter.subject_id != subject_id:
        raise HTTPException(422, FOREIGN_CHAPTER)
    return chapter


def _clean_name(name: str | None) -> str:
    name = (name or "").strip()
    if not 1 <= len(name) <= NAME_LIMIT:
        raise HTTPException(422, INVALID_CHAPTER_NAME)
    return name


def _named(session: Session, subject_id: int, name: str) -> Chapter | None:
    folded = name.casefold()
    for chapter in session.scalars(select(Chapter).where(Chapter.subject_id == subject_id)):
        if chapter.name is not None and chapter.name.strip().casefold() == folded:
            return chapter
    return None


def _ordered(query):
    return query.order_by(Chapter.is_default.desc(), Chapter.ordinal, Chapter.id)


def chapter_counts(
    session: Session, chapter_ids: list[int]
) -> tuple[dict[int, int], dict[int, int], dict[int, int]]:
    """Documents, active topics and live questions per chapter: one grouped statement each."""
    if not chapter_ids:
        return {}, {}, {}
    documents = dict(
        session.execute(
            select(Document.chapter_id, func.count())
            .where(Document.chapter_id.in_(chapter_ids))
            .group_by(Document.chapter_id)
        ).all()
    )
    topics = dict(
        session.execute(
            select(Topic.chapter_id, func.count())
            .where(Topic.chapter_id.in_(chapter_ids), Topic.status == "active")
            .group_by(Topic.chapter_id)
        ).all()
    )
    questions = dict(
        session.execute(
            select(Topic.chapter_id, func.count())
            .select_from(QuestionVersion)
            .join(QuestionFamily, QuestionFamily.id == QuestionVersion.family_id)
            .join(Topic, Topic.id == QuestionFamily.primary_topic_id)
            .where(
                Topic.chapter_id.in_(chapter_ids),
                QuestionVersion.status.in_(COUNTED_QUESTION_STATUSES),
            )
            .group_by(Topic.chapter_id)
        ).all()
    )
    return documents, topics, questions


def _outs(session: Session, chapters: list[Chapter]) -> list[ChapterOut]:
    documents, topics, questions = chapter_counts(session, [c.id for c in chapters])
    return [
        ChapterOut(
            id=c.id,
            subject_id=c.subject_id,
            name=c.name,
            is_default=c.is_default,
            ordinal=c.ordinal,
            document_count=documents.get(c.id, 0),
            topic_count=topics.get(c.id, 0),
            question_count=questions.get(c.id, 0),
        )
        for c in chapters
    ]


def chapter_out(session: Session, chapter: Chapter) -> ChapterOut:
    return _outs(session, [chapter])[0]


def list_chapters(session: Session, subject_id: int) -> list[ChapterOut]:
    _subject(session, subject_id)
    chapters = list(
        session.scalars(_ordered(select(Chapter).where(Chapter.subject_id == subject_id)))
    )
    return _outs(session, chapters)


def chapter_counts_by_subject(session: Session, subject_id: int | None = None) -> dict[int, int]:
    query = select(Chapter.subject_id, func.count()).group_by(Chapter.subject_id)
    if subject_id is not None:
        query = query.where(Chapter.subject_id == subject_id)
    return {sid: count for sid, count in session.execute(query)}


def _new_chapter(session: Session, subject_id: int, name: str) -> Chapter:
    ensure_default_chapter(session, subject_id)
    ordinal = session.scalar(
        select(func.max(Chapter.ordinal)).where(Chapter.subject_id == subject_id)
    )
    chapter = Chapter(
        subject_id=subject_id, name=name, is_default=False, ordinal=(ordinal or 0) + 1
    )
    session.add(chapter)
    session.flush()
    return chapter


def create_chapter(session: Session, subject_id: int, name: str) -> ChapterOut:
    _subject(session, subject_id)
    name = _clean_name(name)
    if _named(session, subject_id, name) is not None:
        raise HTTPException(409, DUPLICATE_CHAPTER)
    chapter = _new_chapter(session, subject_id, name)
    session.commit()
    return chapter_out(session, chapter)


def rename_chapter(session: Session, chapter_id: int, name: str) -> ChapterOut:
    chapter = get_chapter(session, chapter_id)
    name = _clean_name(name)
    same = _named(session, chapter.subject_id, name)
    if same is not None and same.id != chapter.id:
        raise HTTPException(409, DUPLICATE_CHAPTER)
    chapter.name = name
    session.commit()
    return chapter_out(session, chapter)


def delete_chapter(session: Session, chapter_id: int) -> None:
    chapter = get_chapter(session, chapter_id)
    if chapter.is_default:
        raise HTTPException(409, DEFAULT_CHAPTER)
    busy = session.scalar(
        select(
            select(Document.id).where(Document.chapter_id == chapter.id).exists()
            | select(Topic.id)
            .where(Topic.chapter_id == chapter.id, Topic.status == "active")
            .exists()
        )
    )
    if busy:
        raise HTTPException(409, CHAPTER_NOT_EMPTY)
    # Removed topics left here keep a chapter that exists.
    default = ensure_default_chapter(session, chapter.subject_id)
    for topic in session.scalars(select(Topic).where(Topic.chapter_id == chapter.id)):
        topic.chapter_id = default
    session.delete(chapter)
    session.commit()


def resolve_upload_chapter(
    session: Session, subject_id: int, chapter_id: int | None, chapter_name: str | None
) -> int:
    """The chapter an upload goes to: the given one, the one with that name, a new one with
    that name, or the default. A blank name counts as no name."""
    chapter_name = (chapter_name or "").strip() or None
    if chapter_id is not None and chapter_name is not None:
        raise HTTPException(422, MOVE_TARGET)
    if chapter_id is not None:
        return subject_chapter(session, subject_id, chapter_id).id
    if chapter_name is not None:
        name = _clean_name(chapter_name)
        existing = _named(session, subject_id, name)
        return existing.id if existing is not None else _new_chapter(session, subject_id, name).id
    return ensure_default_chapter(session, subject_id)


def delete_subject_chapters(session: Session, subject_id: int) -> None:
    session.execute(delete(Chapter).where(Chapter.subject_id == subject_id))


def _refuse_while_jobs_run(session: Session, subject_id: int) -> None:
    for kind in WORK_KINDS:
        if jobs.find_live_job(session, kind, subject_id=subject_id) is not None:
            raise HTTPException(409, JOBS_RUNNING)


def topic_material_chapters(session: Session, subject_id: int) -> dict[int, set[int]]:
    """Every topic of the subject → the chapters of its non-excluded passages' documents."""
    material: dict[int, set[int]] = {}
    for topic_id, chapter_id in session.execute(
        select(TopicPassage.topic_id, Document.chapter_id)
        .join(Topic, Topic.id == TopicPassage.topic_id)
        .join(SourcePassage, SourcePassage.id == TopicPassage.passage_id)
        .join(DocumentVersion, DocumentVersion.id == SourcePassage.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(Topic.subject_id == subject_id, SourcePassage.excluded.is_(False))
        .group_by(TopicPassage.topic_id, Document.chapter_id)
    ):
        if chapter_id is not None:
            material.setdefault(topic_id, set()).add(chapter_id)
    return material


def move_document(session: Session, document_id: int, body: MoveDocumentIn) -> MoveResult:
    """Moves the document, then every topic whose material is now all in one other chapter
    (removed topics too). A topic spanning chapters, or without passages, stays where it is."""
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(404, DOCUMENT_NOT_FOUND)
    name = (body.new_chapter_name or "").strip() or None
    if (body.chapter_id is None) == (name is None):
        raise HTTPException(422, MOVE_TARGET)
    subject_id = document.subject_id
    if body.chapter_id is not None:
        target = subject_chapter(session, subject_id, body.chapter_id)
    else:
        target = _named(session, subject_id, _clean_name(name))
    _refuse_while_jobs_run(session, subject_id)
    if target is None:
        target = _new_chapter(session, subject_id, _clean_name(name))
    document.chapter_id = target.id
    session.flush()
    material = topic_material_chapters(session, subject_id)
    moved: list[int] = []
    topics = session.scalars(
        select(Topic).where(Topic.subject_id == subject_id).order_by(Topic.ordinal, Topic.id)
    )
    for topic in topics:
        chapters = material.get(topic.id, set())
        if len(chapters) != 1:
            continue
        (only,) = chapters
        if only == topic.chapter_id:
            continue
        topic.chapter_id = only
        if only == target.id and topic.status == "active":
            moved.append(topic.id)
    session.commit()
    return MoveResult(document_id=document.id, chapter_id=target.id, topics_moved=moved)
