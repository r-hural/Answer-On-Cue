"""Replacing a document with a new version: passage matching, a preview of what changes
(staged file, no database row), and the apply that carries topic links and exclusions over
and marks the questions citing changed or removed passages for review."""

import contextlib
import hashlib
import json
import re
import shutil
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.features.chapters import service as chapters
from app.features.languages.profiles import LanguageProfile
from app.features.languages.service import profile_for_subject
from app.features.library import service as library
from app.features.library.extraction import FAILED_MESSAGE, ExtractionResult, PassageDraft
from app.features.library.models import Document, DocumentVersion, SourcePassage
from app.features.library.schemas import VersionApplied, VersionChange
from app.features.questions.models import QuestionFamily, QuestionVersion, RubricVersion
from app.features.topics.models import Topic, TopicPassage

STAGING_FOLDER = "staging/versions"
STALE_STAGING_SECONDS = 24 * 3600
TOKEN = re.compile(r"^[0-9a-f]{32}$")
LIVE_STATUSES = ("active", "needs_review")

SOURCE_CHANGED = "source changed: „{title}” version {version}"
SOURCE_CHANGED_PREFIX = library.SOURCE_CHANGED_PREFIX
UPLOAD_EXPIRED = "This upload expired. Choose the file again."
VERSION_CHANGED = "The document or its questions changed since you looked. Check the new summary."


def passage_key(text: str) -> str:
    """Passages compare by their text: Unicode NFC, whitespace collapsed."""
    normalized = " ".join(unicodedata.normalize("NFC", text).split())
    return hashlib.sha1(normalized.encode()).hexdigest()


@dataclass(frozen=True)
class PassageMatch:
    unchanged: list[tuple[int, int]]  # (old passage id, new draft index)
    changed: list[tuple[int, int]]
    removed: list[int]  # old passage ids
    new: list[int]  # new draft indexes


def match_passages(old: list[SourcePassage], new: list[PassageDraft]) -> PassageMatch:
    """Equal blocks are unchanged; in a replaced block old and new pair up in order as changed,
    the rest is removed (old) or new (new). Locators and headings are not compared."""
    old = sorted(old, key=lambda p: p.ordinal)
    old_keys = [passage_key(p.text) for p in old]
    new_keys = [passage_key(d.text) for d in new]
    unchanged: list[tuple[int, int]] = []
    changed: list[tuple[int, int]] = []
    removed: list[int] = []
    added: list[int] = []
    matcher = SequenceMatcher(None, old_keys, new_keys, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            unchanged.extend((old[i1 + k].id, j1 + k) for k in range(i2 - i1))
            continue
        pairs = min(i2 - i1, j2 - j1) if tag == "replace" else 0
        changed.extend((old[i1 + k].id, j1 + k) for k in range(pairs))
        removed.extend(old[i].id for i in range(i1 + pairs, i2))
        added.extend(range(j1 + pairs, j2))
    return PassageMatch(unchanged, changed, removed, added)


def cited_passage_ids(rubric: dict) -> set[int]:
    """Every passage a rubric cites: essential points, enrichment points and source issues."""
    return {
        int(i)
        for key in ("essential_points", "enrichment_points", "source_issues")
        for item in rubric.get(key) or []
        for i in item.get("passage_ids") or []
    }


def _lineage(session: Session, document_id: int, latest_id: int) -> dict[int, int | None]:
    """Every passage of the document → its successor in the latest version (itself when it is
    there), None when the chain ends before it: one statement."""
    rows = session.execute(
        select(SourcePassage.id, SourcePassage.previous_id, SourcePassage.document_version_id)
        .join(DocumentVersion, DocumentVersion.id == SourcePassage.document_version_id)
        .where(DocumentVersion.document_id == document_id)
    ).all()
    successor = {previous: pid for pid, previous, _ in rows if previous is not None}
    latest = {pid for pid, _, version_id in rows if version_id == latest_id}
    current: dict[int, int | None] = {}
    for pid, _, _ in rows:
        found, seen = pid, set()
        while found not in latest and found in successor and found not in seen:
            seen.add(found)
            found = successor[found]
        current[pid] = found if found in latest else None
    return current


@dataclass
class _Plan:
    document: Document
    latest: DocumentVersion
    result: ExtractionResult
    match: PassageMatch
    excluded: set[int]  # excluded passages of the latest version
    lineage: dict[int, int | None]
    questions: list[int]  # question version ids to mark, sorted
    links: list[tuple[int, int]]  # (topic id, passage id) of the document's passages
    topics_without_material: int = 0
    sha256: str = ""
    fingerprint: str = ""
    successor: dict[int, int] = field(default_factory=dict)  # latest passage id -> draft index


def _plan(
    session: Session, document: Document, filename: str, data: bytes, result: ExtractionResult
) -> _Plan:
    latest = document.versions[-1]
    old = list(latest.passages)
    match = match_passages(old, result.passages)
    lineage = _lineage(session, document.id, latest.id)
    touched = {pid for pid, _ in match.changed} | set(match.removed)
    questions = sorted(
        version_id
        for version_id, rubric_json in session.execute(
            select(QuestionVersion.id, RubricVersion.rubric_json)
            .join(QuestionFamily, QuestionFamily.id == QuestionVersion.family_id)
            .join(RubricVersion, RubricVersion.question_version_id == QuestionVersion.id)
            .where(
                QuestionFamily.subject_id == document.subject_id,
                QuestionVersion.status.in_(LIVE_STATUSES),
            )
        )
        if any(lineage.get(pid) in touched for pid in cited_passage_ids(json.loads(rubric_json)))
    )
    links = [
        (topic_id, passage_id)
        for topic_id, passage_id in session.execute(
            select(TopicPassage.topic_id, TopicPassage.passage_id)
            .join(Topic, Topic.id == TopicPassage.topic_id)
            .where(Topic.subject_id == document.subject_id, Topic.status == "active")
        )
    ]
    plan = _Plan(
        document=document,
        latest=latest,
        result=result,
        match=match,
        excluded={p.id for p in old if p.excluded},
        lineage=lineage,
        questions=questions,
        links=links,
        sha256=hashlib.sha256(data).hexdigest(),
        successor={pid: j for pid, j in [*match.unchanged, *match.changed]},
    )
    plan.topics_without_material = _topics_without_material(plan)
    plan.fingerprint = hashlib.sha256(
        json.dumps([latest.id, plan.sha256, questions]).encode()
    ).hexdigest()[:16]
    return plan


def _carried(plan: _Plan, passage_id: int) -> int | None:
    """The draft index a linked passage moves to; None when it is removed. Raises KeyError for
    a passage not on the document's current line (another document, or a broken chain)."""
    current = plan.lineage[passage_id]
    if current is None:
        raise KeyError(passage_id)
    return plan.successor.get(current)


def _topics_without_material(plan: _Plan) -> int:
    kept: dict[int, bool] = {}
    for topic_id, passage_id in plan.links:
        try:
            keeps = _carried(plan, passage_id) is not None
        except KeyError:
            keeps = True  # a link this replace does not touch
        kept[topic_id] = kept.get(topic_id, False) or keeps
    touched_topics = {
        topic_id for topic_id, passage_id in plan.links if plan.lineage.get(passage_id) is not None
    }
    return sum(1 for topic_id in touched_topics if not kept[topic_id])


def _change(plan: _Plan, token: str, filename: str) -> VersionChange:
    match = plan.match
    carried = {pid for pid, _ in [*match.unchanged, *match.changed]}
    return VersionChange(
        token=token,
        document_id=plan.document.id,
        filename=filename,
        current_version=plan.latest.version,
        new_version=plan.latest.version + 1,
        status=plan.result.status,
        message=plan.result.message,
        page_count=plan.result.page_count,
        passage_count=len(plan.result.passages),
        unchanged=len(match.unchanged),
        changed=len(match.changed),
        removed=len(match.removed),
        new=len(match.new),
        exclusions_carried=len(plan.excluded & carried),
        exclusions_total=len(plan.excluded),
        questions_to_review=len(plan.questions),
        topics_without_material=plan.topics_without_material,
        fingerprint=plan.fingerprint,
    )


def _checked_upload(session: Session, document_id: int, filename: str, data: bytes):
    document = library.get_document(session, document_id)
    extension = library.validate_upload(filename, data)
    if hashlib.sha256(data).hexdigest() == document.versions[-1].sha256:
        raise HTTPException(409, library.IDENTICAL_VERSION)
    chapters._refuse_while_jobs_run(session, document.subject_id)
    return document, extension


def _readable(data: bytes, filename: str, profile: LanguageProfile) -> ExtractionResult:
    """The new file's passages; a file that was not read (damaged, password-protected, a scan
    or too little text) cannot replace a version: its links and questions would be lost."""
    result = library.extract_upload(data, filename, profile=profile)
    if result.status != "imported":
        raise HTTPException(422, result.message or FAILED_MESSAGE)
    return result


def _apply(
    session: Session, settings: Settings, plan: _Plan, filename: str, extension: str, data: bytes
) -> VersionApplied:
    """One transaction: the version and its passages, predecessors and exclusions, the topic
    links moved, the questions marked; the stored file is written before the commit and
    removed again when anything fails."""
    document, latest, match = plan.document, plan.latest, plan.match
    number = latest.version + 1
    version = library.build_version(document, number, filename, extension, data, plan.result)
    version.carried_over = True
    predecessor = {j: pid for pid, j in [*match.unchanged, *match.changed]}
    for index, passage in enumerate(version.passages):
        previous = predecessor.get(index)
        passage.previous_id = previous
        passage.excluded = previous in plan.excluded
    stored = settings.data_dir / version.stored_path
    try:
        session.add(version)
        session.flush()
        new_ids = [p.id for p in version.passages]
        relinked = _move_links(session, plan, new_ids)
        _mark_questions(session, plan, document.title, number)
        library.write_file(settings, version.stored_path, data)
        session.commit()
    except BaseException:
        session.rollback()
        with contextlib.suppress(OSError):
            stored.unlink()
        raise
    session.refresh(document)
    return VersionApplied(
        document=library.document_out(session, document),
        questions_to_review=len(plan.questions),
        topics_relinked=relinked,
    )


def _move_links(session: Session, plan: _Plan, new_ids: list[int]) -> int:
    """Topics link only the latest version: unchanged and changed passages hand their links to
    their successor, removed ones lose them. Returns the number of topics given new links."""
    moved: dict[int, set[int]] = {}
    stale: list[tuple[int, int]] = []
    for topic_id, passage_id in plan.links:
        try:
            index = _carried(plan, passage_id)
        except KeyError:
            continue
        stale.append((topic_id, passage_id))
        if index is not None:
            moved.setdefault(topic_id, set()).add(new_ids[index])
    for topic_id, passage_id in stale:
        session.delete(session.get(TopicPassage, (topic_id, passage_id)))
    session.flush()
    for topic_id, passage_ids in moved.items():
        for passage_id in sorted(passage_ids):
            session.add(TopicPassage(topic_id=topic_id, passage_id=passage_id))
    session.flush()
    return len(moved)


def _mark_questions(session: Session, plan: _Plan, title: str, number: int) -> None:
    if not plan.questions:
        return
    note = SOURCE_CHANGED.format(title=title, version=number)
    for question in session.scalars(
        select(QuestionVersion).where(QuestionVersion.id.in_(plan.questions))
    ):
        question.status = "needs_review"
        question.review_note = f"{question.review_note}; {note}" if question.review_note else note


# --- staging -------------------------------------------------------------------------------


def _staging_root(settings: Settings) -> Path:
    return settings.data_dir / STAGING_FOLDER


def _staging(settings: Settings, token: str) -> Path:
    if not TOKEN.fullmatch(token):
        raise HTTPException(404, UPLOAD_EXPIRED)
    return _staging_root(settings) / token


def _remove_stale_staging(settings: Settings, now: float | None = None) -> None:
    root = _staging_root(settings)
    if not root.is_dir():
        return
    limit = (time.time() if now is None else now) - STALE_STAGING_SECONDS
    for folder in root.iterdir():
        with contextlib.suppress(OSError):
            if not (TOKEN.fullmatch(folder.name) and folder.is_dir()):
                continue
            newest = max([folder.stat().st_mtime, *(p.stat().st_mtime for p in folder.iterdir())])
            if newest < limit:
                shutil.rmtree(folder)


def _stage(settings: Settings, document_id: int, filename: str, extension: str, data: bytes):
    token = uuid.uuid4().hex
    folder = _staging_root(settings) / token
    folder.mkdir(parents=True)
    try:
        (folder / f"upload{extension}").write_bytes(data)
        meta = {
            "document_id": document_id,
            "filename": filename,
            "extension": extension,
            "sha256": hashlib.sha256(data).hexdigest(),
            "created_at": datetime.now(UTC).isoformat(),
        }
        (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return token


def _read_staged(settings: Settings, document_id: int, token: str) -> tuple[dict, bytes]:
    folder = _staging(settings, token)
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        known = meta["extension"] in library.ALLOWED_EXTENSIONS
        if meta["document_id"] != document_id or not known:
            raise HTTPException(404, UPLOAD_EXPIRED)
        data = (folder / f"upload{meta['extension']}").read_bytes()
    except (OSError, ValueError, KeyError):
        raise HTTPException(404, UPLOAD_EXPIRED) from None
    if hashlib.sha256(data).hexdigest() != meta["sha256"]:
        raise HTTPException(404, UPLOAD_EXPIRED)
    return meta, data


# --- entry points --------------------------------------------------------------------------


def preview_version(
    session: Session, settings: Settings, document_id: int, filename: str, data: bytes
) -> VersionChange:
    """Stages the file and says what would change; writes no database row."""
    document, extension = _checked_upload(session, document_id, filename, data)
    result = _readable(data, filename, profile_for_subject(session, document.subject_id))
    plan = _plan(session, document, filename, data, result)
    _remove_stale_staging(settings)
    token = _stage(settings, document.id, filename, extension, data)
    return _change(plan, token, filename)


def apply_version(
    session: Session, settings: Settings, document_id: int, token: str, fingerprint: str
) -> VersionApplied:
    library.get_document(session, document_id)
    meta, data = _read_staged(settings, document_id, token)
    filename = meta["filename"]
    document, extension = _checked_upload(session, document_id, filename, data)
    result = _readable(data, filename, profile_for_subject(session, document.subject_id))
    plan = _plan(session, document, filename, data, result)
    if plan.fingerprint != fingerprint:
        change = _change(plan, token, filename)
        raise HTTPException(409, {"message": VERSION_CHANGED, "preview": change.model_dump()})
    applied = _apply(session, settings, plan, filename, extension, data)
    shutil.rmtree(_staging(settings, token), ignore_errors=True)
    return applied


def discard_version(settings: Settings, document_id: int, token: str) -> None:
    if TOKEN.fullmatch(token):
        shutil.rmtree(_staging_root(settings) / token, ignore_errors=True)


def add_version_now(
    session: Session, settings: Settings, document_id: int, filename: str, data: bytes
):
    """The one-step upload: the same carry-over and marking, without a preview."""
    document, extension = _checked_upload(session, document_id, filename, data)
    result = _readable(data, filename, profile_for_subject(session, document.subject_id))
    plan = _plan(session, document, filename, data, result)
    return _apply(session, settings, plan, filename, extension, data).document
