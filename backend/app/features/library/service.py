import contextlib
import hashlib
import json
import os
import shutil
import unicodedata
import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import delete, distinct, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.features.chapters import service as chapters
from app.features.jobs.models import AiJob
from app.features.languages import service as languages
from app.features.languages.profiles import LanguageProfile
from app.features.library import extraction
from app.features.library.extraction import UnsupportedFileType
from app.features.library.models import Document, DocumentVersion, SourcePassage, Subject
from app.features.library.schemas import (
    DeletionPreview,
    DocumentDetailOut,
    DocumentOut,
    PassageOut,
    PassagePage,
    PreviewOut,
    SubjectOut,
    VersionOut,
)
from app.features.providers.usage import detach_subject
from app.features.questions.models import QuestionFamily, QuestionVersion, RubricVersion
from app.features.recording import retention
from app.features.recording.models import Recording
from app.features.rounds.models import Attempt, Round, RoundItem
from app.features.topics.models import LearningObjective, Topic, TopicPassage

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}
PREVIEW_CHARS = 5000
EXTRACT_KIND = "topics.extract"  # job kinds read for the version flags
GENERATE_KIND = "questions.generate"

SUBJECT_NOT_FOUND = "Subject not found."
DOCUMENT_NOT_FOUND = "Document not found."
VERSION_NOT_FOUND = "Document version not found."
PASSAGE_NOT_FOUND = "Passage not found."
DUPLICATE_SUBJECT = "A subject with this name already exists."
INVALID_NAME = "The subject name must be 1 to 200 characters long."
UNSUPPORTED_TYPE = "Supported formats: PDF, DOCX, TXT, MD."
TOO_LARGE = "The file is too large (limit 25 MB)."
EMPTY_FILE = "The file is empty."
IDENTICAL_VERSION = "This file is identical to the latest version of the document."
SOURCE_CHANGED_PREFIX = "source changed: "  # the review note of a replaced document
SUBJECT_BUSY = "Pause or end the running round of this subject first."
SUBJECT_CHANGED = "This subject changed since you looked. Check the new summary."
CONFIRM_DELETE = (
    "Confirm the deletion: all documents and the study history of this subject will be deleted."
)


# Letters NFKD does not decompose into a base letter and a mark.
_UNDECOMPOSED = str.maketrans({"ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ø": "o", "Ø": "O"})


def neutral_sort_key(text: str) -> tuple[str, str, str]:
    """One order for every language: accents ignored (ł sorts with l), case ignored, then the
    casefolded original and the original break ties."""
    decomposed = unicodedata.normalize("NFKD", text.translate(_UNDECOMPOSED))
    base = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return (base.casefold(), text.casefold(), text)


# --- subjects ------------------------------------------------------------------------------


def _clean_name(name: str) -> str:
    name = name.strip()
    if not 1 <= len(name) <= 200:
        raise HTTPException(422, INVALID_NAME)
    return name


def _ensure_unique_name(session: Session, name: str, own_id: int | None = None) -> None:
    folded = name.casefold()
    for subject_id, existing in session.execute(select(Subject.id, Subject.name)):
        if subject_id != own_id and existing.casefold() == folded:
            raise HTTPException(409, DUPLICATE_SUBJECT)


def get_subject(session: Session, subject_id: int) -> Subject:
    subject = session.get(Subject, subject_id)
    if subject is None:
        raise HTTPException(404, SUBJECT_NOT_FOUND)
    return subject


def _document_counts(session: Session) -> dict[int, int]:
    rows = session.execute(select(Document.subject_id, func.count()).group_by(Document.subject_id))
    return {subject_id: count for subject_id, count in rows}


def _source_review_counts(session: Session, subject_id: int | None = None) -> dict[int, int]:
    """Questions in review because a document was replaced, per subject: one statement."""
    query = (
        select(QuestionFamily.subject_id, func.count())
        .join(QuestionVersion, QuestionVersion.family_id == QuestionFamily.id)
        .where(
            QuestionVersion.status == "needs_review",
            QuestionVersion.review_note.contains(SOURCE_CHANGED_PREFIX, autoescape=True),
        )
        .group_by(QuestionFamily.subject_id)
    )
    if subject_id is not None:
        query = query.where(QuestionFamily.subject_id == subject_id)
    return dict(session.execute(query).all())


def _subject_out(
    subject: Subject, document_count: int, chapter_count: int = 1, source_review_count: int = 0
) -> SubjectOut:
    return SubjectOut(
        id=subject.id,
        name=subject.name,
        description=subject.description,
        document_count=document_count,
        created_at=subject.created_at,
        updated_at=subject.updated_at,
        chapter_count=chapter_count,
        source_review_count=source_review_count,
        language=subject.language,
    )


def list_subjects(session: Session) -> list[SubjectOut]:
    counts = _document_counts(session)
    chapter_counts = chapters.chapter_counts_by_subject(session)
    reviews = _source_review_counts(session)
    subjects = session.scalars(select(Subject)).all()
    subjects.sort(key=lambda s: neutral_sort_key(s.name))
    return [
        _subject_out(s, counts.get(s.id, 0), chapter_counts.get(s.id, 1), reviews.get(s.id, 0))
        for s in subjects
    ]


def subject_out(session: Session, subject: Subject) -> SubjectOut:
    count = session.scalar(
        select(func.count()).select_from(Document).where(Document.subject_id == subject.id)
    )
    chapter_count = chapters.chapter_counts_by_subject(session, subject.id).get(subject.id, 1)
    reviews = _source_review_counts(session, subject.id).get(subject.id, 0)
    return _subject_out(subject, count or 0, chapter_count, reviews)


def create_subject(
    session: Session, name: str, description: str | None, language: str
) -> SubjectOut:
    name = _clean_name(name)
    languages.require_enabled(session, language)
    _ensure_unique_name(session, name)
    subject = Subject(name=name, description=description, language=language)
    session.add(subject)
    session.commit()
    session.refresh(subject)
    return _subject_out(subject, 0)


def update_subject(
    session: Session, subject_id: int, name: str | None, description: str | None, set_fields: set
) -> SubjectOut:
    subject = get_subject(session, subject_id)
    if "name" in set_fields and name is not None:
        name = _clean_name(name)
        _ensure_unique_name(session, name, own_id=subject.id)
        subject.name = name
    if "description" in set_fields:
        subject.description = description
    session.commit()
    session.refresh(subject)
    return subject_out(session, subject)


def _subject_counts(session: Session, subject_id: int) -> tuple[int, ...]:
    """documents, passages, topics, objectives, questions, question versions, rounds, answers:
    one statement, so the cost does not grow with the data."""

    def count(query):
        return query.where(Document.subject_id == subject_id).scalar_subquery()

    documents = select(func.count()).select_from(Document)
    passages = (
        select(func.count())
        .select_from(SourcePassage)
        .join(DocumentVersion, DocumentVersion.id == SourcePassage.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
    )
    topics = select(func.count()).select_from(Topic).where(Topic.subject_id == subject_id)
    objectives = (
        select(func.count())
        .select_from(LearningObjective)
        .join(Topic, Topic.id == LearningObjective.topic_id)
        .where(Topic.subject_id == subject_id)
    )
    questions = (
        select(func.count())
        .select_from(QuestionFamily)
        .where(QuestionFamily.subject_id == subject_id)
    )
    versions = (
        select(func.count())
        .select_from(QuestionVersion)
        .join(QuestionFamily, QuestionFamily.id == QuestionVersion.family_id)
        .where(QuestionFamily.subject_id == subject_id)
    )
    rounds = select(func.count()).select_from(Round).where(Round.subject_id == subject_id)
    answers = (
        select(func.count())
        .select_from(Attempt)
        .join(RoundItem, RoundItem.id == Attempt.round_item_id)
        .join(Round, Round.id == RoundItem.round_id)
        .where(Round.subject_id == subject_id, Attempt.outcome != "pending")
    )
    row = session.execute(
        select(
            count(documents),
            count(passages),
            topics.scalar_subquery(),
            objectives.scalar_subquery(),
            questions.scalar_subquery(),
            versions.scalar_subquery(),
            rounds.scalar_subquery(),
            answers.scalar_subquery(),
        )
    ).one()
    return tuple(row)


def _collect(
    session: Session, settings: Settings, subject: Subject
) -> tuple[DeletionPreview, list[tuple[int, str]]]:
    """The preview and the subject's recording rows (id, stored path), counted by the very
    rules the deletion uses."""
    (documents, passages, topics, objectives, questions, question_versions, rounds, answers) = (
        _subject_counts(session, subject.id)
    )
    version_rows = session.execute(
        select(DocumentVersion.stored_path)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(Document.subject_id == subject.id)
    ).all()
    recording_rows = session.execute(
        select(
            Recording.id, Recording.stored_path, Recording.size_bytes, Recording.audio_deleted_at
        )
        .join(Attempt, Attempt.recording_id == Recording.id)
        .join(RoundItem, RoundItem.id == Attempt.round_item_id)
        .join(Round, Round.id == RoundItem.round_id)
        .where(Round.subject_id == subject.id)
        .order_by(Recording.id)
    ).all()
    files = 0
    size = 0
    for (stored_path,) in version_rows:
        path = retention.data_file(settings, stored_path)
        if path is not None and path.is_file():
            files += 1
            size += path.stat().st_size
    with_audio = [row for row in recording_rows if row.audio_deleted_at is None]
    preview = DeletionPreview(
        subject=subject.name,
        documents=documents,
        versions=len(version_rows),
        passages=passages,
        files=files,
        bytes=size,
        topics=topics,
        objectives=objectives,
        questions=questions,
        question_versions=question_versions,
        rounds=rounds,
        answers=answers,
        recordings=len(with_audio),
        recording_bytes=sum(row.size_bytes for row in with_audio),
    )
    preview.fingerprint = _fingerprint(preview, [row.id for row in recording_rows])
    return preview, [(row.id, row.stored_path) for row in recording_rows]


def _fingerprint(preview: DeletionPreview, recording_ids: list[int]) -> str:
    """Stands for the summary shown, so a confirmation only applies to what was on screen."""
    summary = preview.model_dump(exclude={"fingerprint"})
    summary["recording_ids"] = recording_ids
    return hashlib.sha256(json.dumps(summary, sort_keys=True).encode()).hexdigest()[:16]


def deletion_preview(session: Session, settings: Settings, subject: Subject) -> DeletionPreview:
    return _collect(session, settings, subject)[0]


def delete_subject(
    session: Session,
    settings: Settings,
    subject_id: int,
    confirm: bool,
    fingerprint: str | None = None,
) -> DeletionPreview:
    subject = get_subject(session, subject_id)
    if not confirm:
        raise HTTPException(400, CONFIRM_DELETE)
    if session.scalar(
        select(Round.id).where(Round.subject_id == subject_id, Round.status == "active").limit(1)
    ):
        raise HTTPException(409, SUBJECT_BUSY)
    preview, recordings = _collect(session, settings, subject)
    if fingerprint is not None and fingerprint != preview.fingerprint:
        raise HTTPException(409, {"message": SUBJECT_CHANGED, "preview": preview.model_dump()})
    # The recordings hang on attempts only through SET NULL, so they go after the cascade.
    session.delete(subject)
    session.flush()
    chapters.delete_subject_chapters(session, subject_id)  # no foreign key, so no cascade
    detach_subject(session, subject_id)
    if recordings:
        session.execute(delete(Recording).where(Recording.id.in_([rid for rid, _ in recordings])))
    session.commit()
    for _, stored_path in recordings:
        path = retention.data_file(settings, stored_path)
        if path is not None:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
    shutil.rmtree(_documents_root(settings) / f"subject-{subject_id}", ignore_errors=True)
    return preview


# --- storage -------------------------------------------------------------------------------


def _documents_root(settings: Settings) -> Path:
    return settings.data_dir / "documents"


def stored_relative_path(subject_id: int, document_id: int, version: int, extension: str) -> str:
    """Built only from integers and a whitelisted extension, never from the uploaded name."""
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError(extension)
    return f"documents/subject-{subject_id}/document-{document_id}/v{version}{extension}"


def write_file(settings: Settings, relative_path: str, data: bytes) -> Path:
    target = (settings.data_dir / relative_path).resolve()
    if not target.is_relative_to(_documents_root(settings).resolve()):
        raise ValueError("path outside documents folder")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.parent / f".{uuid.uuid4().hex}.tmp"
    try:
        temp.write_bytes(data)
        os.replace(temp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise
    return target


# --- documents -----------------------------------------------------------------------------


def _file_extension(filename: str) -> str:
    return ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""


def _default_title(filename: str) -> str:
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return (stem.strip() or "Document")[:300]


def validate_upload(filename: str, data: bytes) -> str:
    extension = _file_extension(filename)
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, UNSUPPORTED_TYPE)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, TOO_LARGE)
    if not data:
        raise HTTPException(422, EMPTY_FILE)
    return extension


def read_upload(file_obj) -> bytes:
    """Read at most the limit plus one byte, so an oversized upload is detected cheaply."""
    return file_obj.read(MAX_UPLOAD_BYTES + 1)


def _passage_counts(session: Session, version_ids: list[int]) -> dict[int, int]:
    if not version_ids:
        return {}
    rows = session.execute(
        select(SourcePassage.document_version_id, func.count())
        .where(SourcePassage.document_version_id.in_(version_ids))
        .group_by(SourcePassage.document_version_id)
    )
    return {version_id: count for version_id, count in rows}


VersionFlags = tuple[dict[tuple[int, str], object], dict[int, int]]


def _version_flags(session: Session, versions: list[DocumentVersion]) -> VersionFlags:
    """The newest done extraction/generation of each version or an earlier version of its
    document (a replaced book stays analysed), and its topic count: two statements, whatever
    the number of versions."""
    if not versions:
        return {}, {}
    version_ids = [v.id for v in versions]
    finished = {
        (version_id, kind): at
        for version_id, kind, at in session.execute(
            select(AiJob.document_version_id, AiJob.kind, func.max(AiJob.updated_at))
            .where(
                AiJob.document_version_id.in_(version_ids),
                AiJob.status == "done",
                AiJob.kind.in_((EXTRACT_KIND, GENERATE_KIND)),
            )
            .group_by(AiJob.document_version_id, AiJob.kind)
        )
    }
    topics = dict(
        session.execute(
            select(SourcePassage.document_version_id, func.count(distinct(Topic.id)))
            .join(TopicPassage, TopicPassage.passage_id == SourcePassage.id)
            .join(Topic, Topic.id == TopicPassage.topic_id)
            .where(
                SourcePassage.document_version_id.in_(version_ids),
                SourcePassage.excluded.is_(False),
                Topic.status == "active",
            )
            .group_by(SourcePassage.document_version_id)
        ).all()
    )
    inherited: dict[tuple[int, str], object] = {}
    for version in versions:
        for kind in (EXTRACT_KIND, GENERATE_KIND):
            times = [
                finished[(earlier.id, kind)]
                for earlier in versions
                if earlier.document_id == version.document_id
                and earlier.version <= version.version
                and (earlier.id, kind) in finished
            ]
            if times:
                inherited[(version.id, kind)] = max(times)
    return inherited, topics


def _version_usage(session: Session, document: Document) -> dict[int, tuple[int, int]]:
    """version id -> (answered attempts, rounds) of the round items whose rubric cites one of
    its passages: three statements, whatever the number of rounds and versions."""
    items = session.execute(
        select(RoundItem.id, RoundItem.round_id, RubricVersion.rubric_json)
        .join(Round, Round.id == RoundItem.round_id)
        .join(RubricVersion, RubricVersion.id == RoundItem.rubric_version_id)
        .where(Round.subject_id == document.subject_id)
    ).all()
    version_of = dict(
        session.execute(
            select(SourcePassage.id, SourcePassage.document_version_id)
            .join(DocumentVersion, DocumentVersion.id == SourcePassage.document_version_id)
            .where(DocumentVersion.document_id == document.id)
        ).all()
    )
    answered = dict(
        session.execute(
            select(Attempt.round_item_id, func.count())
            .join(RoundItem, RoundItem.id == Attempt.round_item_id)
            .join(Round, Round.id == RoundItem.round_id)
            .where(Round.subject_id == document.subject_id, Attempt.outcome == "answered")
            .group_by(Attempt.round_item_id)
        ).all()
    )
    answers: dict[int, int] = {}
    rounds: dict[int, set[int]] = {}
    for item_id, round_id, rubric_json in items:
        count = answered.get(item_id, 0)
        if not count:
            continue
        cited = _cited(json.loads(rubric_json))
        for version_id in {version_of[p] for p in cited if p in version_of}:
            answers[version_id] = answers.get(version_id, 0) + count
            rounds.setdefault(version_id, set()).add(round_id)
    return {v: (answers[v], len(rounds[v])) for v in answers}


def _cited(rubric: dict) -> set[int]:
    return {
        int(i)
        for key in ("essential_points", "enrichment_points", "source_issues")
        for item in rubric.get(key) or []
        for i in item.get("passage_ids") or []
    }


def _version_out(
    version: DocumentVersion,
    passage_count: int,
    flags: VersionFlags = ({}, {}),
    usage: tuple[int, int] = (0, 0),
) -> VersionOut:
    finished, topics = flags
    return VersionOut(
        id=version.id,
        version=version.version,
        filename=version.filename,
        mime=version.mime,
        size_bytes=version.size_bytes,
        status=version.status,
        message=version.error_message,
        char_count=version.char_count,
        page_count=version.page_count,
        passage_count=passage_count,
        created_at=version.created_at,
        topics_extracted_at=finished.get((version.id, EXTRACT_KIND)),
        questions_generated_at=finished.get((version.id, GENERATE_KIND)),
        topic_count=topics.get(version.id, 0),
        answer_count=usage[0],
        round_count=usage[1],
    )


def _document_out(
    document: Document,
    versions: list[DocumentVersion],
    counts: dict[int, int],
    flags: VersionFlags,
) -> DocumentOut:
    latest = versions[-1]
    return DocumentOut(
        id=document.id,
        title=document.title,
        latest_version=_version_out(latest, counts.get(latest.id, 0), flags),
        version_count=len(versions),
        chapter_id=document.chapter_id,
    )


def document_out(session: Session, document: Document) -> DocumentOut:
    versions = list(document.versions)
    ids = [v.id for v in versions]
    return _document_out(
        document, versions, _passage_counts(session, ids), _version_flags(session, versions)
    )


def document_detail(session: Session, document: Document) -> DocumentDetailOut:
    versions = list(document.versions)
    ids = [v.id for v in versions]
    counts = _passage_counts(session, ids)
    flags = _version_flags(session, versions)
    usage = _version_usage(session, document)
    outs = [_version_out(v, counts.get(v.id, 0), flags, usage.get(v.id, (0, 0))) for v in versions]
    return DocumentDetailOut(
        id=document.id,
        title=document.title,
        latest_version=outs[-1],
        version_count=len(outs),
        chapter_id=document.chapter_id,
        versions=outs,
    )


def list_documents(session: Session, subject_id: int) -> list[DocumentOut]:
    """A fixed number of statements, whatever the number of documents and versions."""
    get_subject(session, subject_id)
    documents = session.scalars(
        select(Document).where(Document.subject_id == subject_id).order_by(Document.id)
    ).all()
    versions: dict[int, list[DocumentVersion]] = {d.id: [] for d in documents}
    for version in session.scalars(
        select(DocumentVersion)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(Document.subject_id == subject_id)
        .order_by(DocumentVersion.version)
    ):
        versions[version.document_id].append(version)
    every = [v for items in versions.values() for v in items]
    counts = _passage_counts(session, [v.id for v in every])
    flags = _version_flags(session, every)
    return [_document_out(d, versions[d.id], counts, flags) for d in documents if versions[d.id]]


def _add_version(
    session: Session,
    settings: Settings,
    document: Document,
    version_number: int,
    filename: str,
    extension: str,
    data: bytes,
) -> DocumentVersion:
    result = extract_upload(
        data, filename, profile=languages.profile_for_subject(session, document.subject_id)
    )
    version = build_version(document, version_number, filename, extension, data, result)
    session.add(version)
    session.flush()
    write_file(settings, version.stored_path, data)
    return version


def extract_upload(
    data: bytes, filename: str, *, profile: LanguageProfile
) -> extraction.ExtractionResult:
    try:
        return extraction.extract(data, filename, profile=profile)
    except UnsupportedFileType:
        raise HTTPException(415, UNSUPPORTED_TYPE) from None


def build_version(
    document: Document,
    version_number: int,
    filename: str,
    extension: str,
    data: bytes,
    result: extraction.ExtractionResult,
) -> DocumentVersion:
    """The version row with its passages, not yet added to the session."""
    relative_path = stored_relative_path(
        document.subject_id, document.id, version_number, extension
    )
    version = DocumentVersion(
        document_id=document.id,
        version=version_number,
        filename=filename[:255],
        stored_path=relative_path,
        mime=ALLOWED_EXTENSIONS[extension],
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        status=result.status,
        error_message=result.message,
        extracted_text=result.text,
        char_count=len(result.text),
        page_count=result.page_count,
    )
    for draft in result.passages:
        version.passages.append(
            SourcePassage(
                ordinal=draft.ordinal,
                heading=draft.heading,
                locator=draft.locator,
                text=draft.text,
                char_start=draft.char_start,
                char_end=draft.char_end,
            )
        )
    return version


def create_document(
    session: Session,
    settings: Settings,
    subject_id: int,
    filename: str,
    data: bytes,
    title: str | None,
    *,
    chapter_id: int | None = None,
    chapter_name: str | None = None,
) -> DocumentOut:
    get_subject(session, subject_id)
    extension = validate_upload(filename, data)
    title = (title or "").strip()[:300] or _default_title(filename)
    try:
        target = chapters.resolve_upload_chapter(session, subject_id, chapter_id, chapter_name)
    except BaseException:
        session.rollback()
        raise
    document = Document(subject_id=subject_id, title=title, chapter_id=target)
    session.add(document)
    session.flush()
    try:
        _add_version(session, settings, document, 1, filename, extension, data)
        session.commit()
    except BaseException:
        session.rollback()
        _remove_document_folder(settings, subject_id, document.id)
        raise
    session.refresh(document)
    return document_out(session, document)


def _remove_document_folder(settings: Settings, subject_id: int, document_id: int) -> None:
    folder = _documents_root(settings) / f"subject-{subject_id}" / f"document-{document_id}"
    shutil.rmtree(folder, ignore_errors=True)


def get_document(session: Session, document_id: int) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(404, DOCUMENT_NOT_FOUND)
    return document


def get_document_detail(session: Session, document_id: int) -> DocumentDetailOut:
    return document_detail(session, get_document(session, document_id))


def add_version(
    session: Session, settings: Settings, document_id: int, filename: str, data: bytes
) -> DocumentOut:
    """One step: stores the new version with the same carry-over and review marking as the
    preview and apply of library.versions."""
    from app.features.library import versions

    return versions.add_version_now(session, settings, document_id, filename, data)


def get_version(session: Session, version_id: int) -> DocumentVersion:
    version = session.get(DocumentVersion, version_id)
    if version is None:
        raise HTTPException(404, VERSION_NOT_FOUND)
    return version


def list_passages(session: Session, version_id: int, offset: int, limit: int) -> PassagePage:
    get_version(session, version_id)
    where = SourcePassage.document_version_id == version_id
    total = session.scalar(select(func.count()).select_from(SourcePassage).where(where))
    rows = session.scalars(
        select(SourcePassage)
        .where(where)
        .order_by(SourcePassage.ordinal)
        .offset(offset)
        .limit(limit)
    ).all()
    return PassagePage(total=total or 0, items=[_passage_out(p) for p in rows])


def _passage_out(passage: SourcePassage) -> PassageOut:
    return PassageOut(
        id=passage.id,
        ordinal=passage.ordinal,
        heading=passage.heading,
        locator=passage.locator,
        text=passage.text,
        excluded=passage.excluded,
    )


def version_preview(session: Session, version_id: int) -> PreviewOut:
    version = get_version(session, version_id)
    return PreviewOut(
        status=version.status,
        message=version.error_message,
        char_count=version.char_count,
        page_count=version.page_count,
        text=version.extracted_text[:PREVIEW_CHARS],
    )


def set_passage_excluded(session: Session, passage_id: int, excluded: bool) -> PassageOut:
    passage = session.get(SourcePassage, passage_id)
    if passage is None:
        raise HTTPException(404, PASSAGE_NOT_FOUND)
    passage.excluded = excluded
    session.commit()
    return _passage_out(passage)
