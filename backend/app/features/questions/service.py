"""Question bank: AI generation as a resumable job (one step per topic batch), and the bank."""

import json
from dataclasses import dataclass
from itertools import cycle, islice
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.features.chapters import service as chapters
from app.features.jobs import service as jobs
from app.features.jobs.models import AiJob
from app.features.languages.service import profile_for_subject
from app.features.library import service as library
from app.features.library.models import Document, DocumentVersion, SourcePassage
from app.features.library.versions import passage_key
from app.features.providers.base import TextGenerator
from app.features.providers.types import Message, Provenance
from app.features.questions import prompts, validation
from app.features.questions.models import QuestionFamily, QuestionVersion, RubricVersion
from app.features.questions.prompts import GeneratedQuestion
from app.features.questions.schemas import (
    PassageRef,
    QuestionOut,
    QuestionWithRubric,
    Rubric,
    SourceChange,
    SourcePolicy,
)
from app.features.topics.models import LearningObjective, Topic, TopicPassage
from app.features.topics.service import (
    NOT_IMPORTED,
    SECTION_CHAR_LIMIT,
    ancestors_at,
    finished_before,
    operation_id,
    refuse_replaced,
)
from app.features.topics.service import (
    VERSION_REPLACED as VERSION_REPLACED,  # the per-book refusal of an older version
)

GENERATE_KIND = "questions.generate"
BATCH_SIZE = 5
MAX_OUTPUT_TOKENS = 8000
SOURCE_POLICY = "materials"
LIVE_STATUSES = ("active", "needs_review")
STYLES = ("short", "open")
DIFFICULTIES = ("recall", "apply", "analysis")

NO_TOPICS = "Analyze the materials first to identify the topics."
MISSING_VERIFICATION = "No verification result for this question."
DEFAULT_REVIEW_NOTE = "Verification indicated that the question may not follow from the materials."
DEFAULT_ISSUE_NOTE = "The sources may be unclear or contradict each other."
QUESTION_NOT_FOUND = "Question not found."
EDIT_LATEST = "Edit the latest version of the question."
EDIT_RETIRED = "This question has been retired."
OTHER_POLICY_RUNNING = (
    "A question batch with a different source policy is already running. Wait for it to finish."
)
INVALID_EDIT = "The question cannot be saved"
NO_BOOK_TOPICS = "Analyse this document's topics first."
GENERATION_RUNNING = (
    "Questions are already being generated for this subject. Wait for that to finish."
)
FOREIGN_DOCUMENT = "This document belongs to another subject."
KEEP_REMOVED = (
    "This question cites a passage that is no longer in the document. "
    "Edit it to choose another passage, or retire it."
)
KEEP_NOT_NEEDED = "This question cites only current passages."
NOT_IN_REVIEW = "This question is not waiting for review."
CHECK_USE_KEEP = (
    "This question cites passages of an older document version. "
    "Keep it (still correct) or edit it instead."
)
NO_CHANGED_TOPICS = (
    "No topic has new or changed passages in this version. Analyse its new passages first, "
    "or choose All topics."
)


def slot_specs(style: str, difficulty: str, start: int, count: int) -> list[tuple[str, str]]:
    """(style, difficulty) per question; "mixed" alternates by the question index in the topic."""
    styles = islice(cycle(STYLES), start, start + count) if style == "mixed" else [style] * count
    difficulties = (
        islice(cycle(DIFFICULTIES), start, start + count)
        if difficulty == "mixed"
        else [difficulty] * count
    )
    return list(zip(styles, difficulties, strict=True))


def start_generation(
    session: Session,
    subject_id: int,
    topic_ids: list[int] | None,
    per_topic: int,
    style: str,
    difficulty: str,
    *,
    source_policy: SourcePolicy = "materials",
) -> AiJob:
    return start_generation_job(
        session, subject_id, topic_ids, per_topic, style, difficulty, source_policy=source_policy
    )[0]


def start_generation_job(
    session: Session,
    subject_id: int,
    topic_ids: list[int] | None,
    per_topic: int,
    style: str,
    difficulty: str,
    *,
    source_policy: SourcePolicy = "materials",
    chapter_id: int | None = None,
    document_version_id: int | None = None,
) -> tuple[AiJob, bool]:
    """The job and whether it is new; a double click gets the live job, not a second one.

    `document_version_id` (per book): only topics with material from that version, prompted
    and validated with its passages only. `chapter_id`: only that chapter's topics."""
    _generation_scope(session, subject_id, chapter_id, document_version_id)
    live = jobs.find_live_job(session, GENERATE_KIND, subject_id=subject_id)
    if live is not None:
        live_params = jobs.load_params(live)
        if live_params.get("source_policy", SOURCE_POLICY) != source_policy:
            raise HTTPException(409, OTHER_POLICY_RUNNING)
        live_scope = (live_params.get("document_version_id"), live_params.get("chapter_id"))
        if live_scope != (document_version_id, chapter_id):
            raise HTTPException(409, GENERATION_RUNNING)
        return live, False
    topics = generation_topic_ids(
        session,
        subject_id,
        topic_ids,
        chapter_id=chapter_id,
        document_version_id=document_version_id,
    )
    batches = [
        {
            "topic_id": topic_id,
            "start": start,
            "count": min(BATCH_SIZE, per_topic - start),
            "document_version_id": document_version_id,
        }
        for topic_id in topics
        for start in range(0, per_topic, BATCH_SIZE)
    ]
    params = {
        "subject_id": subject_id,
        "per_topic": per_topic,
        "style": style,
        "difficulty": difficulty,
        "batches": batches,
        "source_policy": source_policy,
        "chapter_id": chapter_id,
        "document_version_id": document_version_id,
    }
    job = jobs.create_job(
        session,
        GENERATE_KIND,
        subject_id,
        params,
        steps_total=len(batches),
        document_version_id=document_version_id,
    )
    return job, True


def _generation_scope(
    session: Session, subject_id: int, chapter_id: int | None, document_version_id: int | None
) -> DocumentVersion | None:
    """Checks the subject, chapter and book of a generation; returns the book's version."""
    library.get_subject(session, subject_id)
    if chapter_id is not None:
        chapters.subject_chapter(session, subject_id, chapter_id)
    if document_version_id is None:
        return None
    version = library.get_version(session, document_version_id)
    document = session.get(Document, version.document_id)
    if document.subject_id != subject_id:
        raise HTTPException(422, FOREIGN_DOCUMENT)
    if chapter_id is not None and document.chapter_id != chapter_id:
        raise HTTPException(422, chapters.FOREIGN_CHAPTER)
    if version.status != "imported":
        raise HTTPException(409, NOT_IMPORTED)
    refuse_replaced(session, version)
    return version


def _changed_material_topics(session: Session, version: DocumentVersion) -> set[int]:
    """Topics with a non-excluded passage of the version that is new or changed since the
    newest earlier version with a finished per-book generation (else since version 1): the
    passage's line starts after that version, or its text differs from its ancestor there."""
    base = finished_before(session, version, GENERATE_KIND) or 1
    rows = session.execute(
        select(TopicPassage.topic_id, SourcePassage.id, SourcePassage.text)
        .join(SourcePassage, SourcePassage.id == TopicPassage.passage_id)
        .where(
            SourcePassage.document_version_id == version.id,
            SourcePassage.excluded.is_(False),
        )
    ).all()
    ancestors = ancestors_at(session, version, base)
    wanted = {a for a in ancestors.values() if a is not None}
    before = dict(
        session.execute(
            select(SourcePassage.id, SourcePassage.text).where(SourcePassage.id.in_(wanted))
        ).all()
    )
    return {
        topic_id
        for topic_id, pid, text in rows
        if ancestors.get(pid) is None or passage_key(before[ancestors[pid]]) != passage_key(text)
    }


def generation_topic_ids(
    session: Session,
    subject_id: int,
    topic_ids: list[int] | None,
    *,
    chapter_id: int | None = None,
    document_version_id: int | None = None,
) -> list[int]:
    """The topics a generation job covers, in batch order, raising the job's HTTP errors.

    Per book: topics with material from that version; for a later version only those whose
    material there is new or changed."""
    version = _generation_scope(session, subject_id, chapter_id, document_version_id)
    query = select(Topic.id).where(Topic.subject_id == subject_id, Topic.status == "active")
    if topic_ids is not None:
        query = query.where(Topic.id.in_(topic_ids))
    if chapter_id is not None:
        query = query.where(Topic.chapter_id == chapter_id)
    if version is not None:
        with_material = (
            select(TopicPassage.topic_id)
            .join(SourcePassage, SourcePassage.id == TopicPassage.passage_id)
            .where(
                SourcePassage.document_version_id == version.id,
                SourcePassage.excluded.is_(False),
            )
        )
        query = query.where(Topic.id.in_(with_material))
    topics = list(session.scalars(query.order_by(Topic.ordinal, Topic.id)))
    if not topics:
        raise HTTPException(409, NO_BOOK_TOPICS if version is not None else NO_TOPICS)
    if version is not None and version.version > 1:
        changed = _changed_material_topics(session, version)
        topics = [t for t in topics if t in changed]
        if not topics:
            raise HTTPException(409, NO_CHANGED_TOPICS)
    return topics


def _topic_passages_query(topic_id: int, version_id: int | None):
    query = (
        select(SourcePassage)
        .join(TopicPassage, TopicPassage.passage_id == SourcePassage.id)
        .where(TopicPassage.topic_id == topic_id, SourcePassage.excluded.is_(False))
    )
    if version_id is not None:
        query = query.where(SourcePassage.document_version_id == version_id)
    return query


def topic_passages(
    session: Session, topic_id: int, version_id: int | None = None
) -> list[SourcePassage]:
    """The topic's non-excluded passages in document order (only the version's, when given),
    capped at one call's char limit."""
    passages = session.scalars(
        _topic_passages_query(topic_id, version_id).order_by(
            SourcePassage.document_version_id, SourcePassage.ordinal
        )
    )
    kept, size = [], 0
    for passage in passages:
        if size + len(passage.text) > SECTION_CHAR_LIMIT:
            break
        kept.append(passage)
        size += len(passage.text)
    return kept


def allowed_passage_ids(session: Session, topic_id: int, version_id: int | None = None) -> set[int]:
    return {p.id for p in session.scalars(_topic_passages_query(topic_id, version_id))}


def subject_passage_ids(session: Session, subject_id: int) -> set[int]:
    return set(
        session.scalars(
            select(SourcePassage.id)
            .join(TopicPassage, TopicPassage.passage_id == SourcePassage.id)
            .join(Topic, Topic.id == TopicPassage.topic_id)
            .where(
                Topic.subject_id == subject_id,
                Topic.status == "active",
                SourcePassage.excluded.is_(False),
            )
        )
    )


def topic_question_texts(session: Session, topic_id: int) -> list[str]:
    return list(
        session.scalars(
            select(QuestionVersion.text)
            .join(QuestionFamily, QuestionFamily.id == QuestionVersion.family_id)
            .where(
                QuestionFamily.primary_topic_id == topic_id,
                QuestionVersion.status.in_(LIVE_STATUSES),
            )
            .order_by(QuestionVersion.id)
        )
    )


def generate_step(
    session: Session, job: AiJob, index: int, generator: TextGenerator
) -> dict[str, Any]:
    params = jobs.load_params(job)
    batch = params["batches"][index]
    policy = params.get("source_policy", SOURCE_POLICY)  # older jobs: materials
    topic = session.get(Topic, batch["topic_id"])
    if topic is None or topic.status != "active":
        return {"topic_id": batch["topic_id"], "saved": 0, "needs_review": 0, "rejected": {}}
    version_id = batch.get("document_version_id")  # per book: that book's passages only
    query = (
        select(LearningObjective)
        .where(LearningObjective.topic_id == topic.id)
        .order_by(LearningObjective.ordinal, LearningObjective.id)
    )
    if version_id is not None:  # per book: that book's objectives and the shared ones
        book = select(DocumentVersion.document_id).where(DocumentVersion.id == version_id)
        query = query.where(
            LearningObjective.document_id.is_(None)
            | LearningObjective.document_id.in_(book.scalar_subquery())
        )
    objectives = list(session.scalars(query))
    passages = topic_passages(session, topic.id, version_id)
    passage_pairs = [(p.id, p.text) for p in passages]
    slots = slot_specs(params["style"], params["difficulty"], batch["start"], batch["count"])
    profile = profile_for_subject(session, topic.subject_id)
    message = prompts.generate_message(
        topic.title,
        topic.description,
        [o.text for o in objectives],
        passage_pairs,
        topic_question_texts(session, topic.id),
        slots,
        policy,
        profile=profile,
    )
    generated = generator.generate_structured(
        GENERATE_KIND,
        prompts.generate_system(policy, profile=profile),
        [Message(role="user", content=message)],
        prompts.GeneratedBatch,
        operation_id=operation_id(job, index),
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    allowed = allowed_passage_ids(session, topic.id, version_id)
    seen = topic_question_texts(session, topic.id)
    accepted: list[GeneratedQuestion] = []
    rejected: dict[str, int] = {}
    for question in generated.value.questions[: batch["count"]]:
        result = validation.validate_question(question, allowed, seen, policy=policy)
        if result.question is None:
            rejected[result.reason] = rejected.get(result.reason, 0) + 1
            continue
        accepted.append(result.question)
        seen.append(result.question.text)

    reviews: list[str | None] = []
    if accepted:
        verification = generator.generate_structured(
            "questions.verify",
            prompts.verify_system(policy, profile=profile),
            [
                Message(
                    role="user",
                    content=prompts.verify_message(
                        passage_pairs,
                        [(q.text, q.rubric) for q in accepted],
                        policy,
                        profile=profile,
                    ),
                )
            ],
            prompts.Verification,
            operation_id=f"{operation_id(job, index)}:verify",
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        reviews = review_notes(verification.value, len(accepted))
        reviews = [issue_note(q, note) for q, note in zip(accepted, reviews, strict=True)]

    for question, note in zip(accepted, reviews, strict=True):
        save_generated(
            session, job, topic, objectives, question, note, generated.provenance, policy
        )
    return {
        "topic_id": topic.id,
        "saved": len(accepted),
        "needs_review": sum(1 for note in reviews if note is not None),
        "rejected": rejected,
    }


def review_notes(verification: prompts.Verification, count: int) -> list[str | None]:
    """None for a question that passed; otherwise the note it is saved with as needs_review."""
    items = {item.index: item for item in verification.items}
    notes: list[str | None] = []
    for index in range(count):
        item = items.get(index)
        if item is None:
            notes.append(MISSING_VERIFICATION)
        elif item.answerable and item.grounded:
            notes.append(None)
        else:
            notes.append(item.note.strip() or DEFAULT_REVIEW_NOTE)
    return notes


def issue_note(question: GeneratedQuestion, note: str | None) -> str | None:
    """Source issues put the question into review; their notes come before the verification's."""
    notes = [i.note.strip() for i in question.rubric.source_issues if i.note.strip()]
    if question.rubric.source_issues and not notes:
        notes = [DEFAULT_ISSUE_NOTE]
    if note is not None:
        notes.append(note)
    return " · ".join(notes) if notes else None


def save_generated(
    session: Session,
    job: AiJob,
    topic: Topic,
    objectives: list[LearningObjective],
    question: GeneratedQuestion,
    review_note: str | None,
    provenance: Provenance,
    source_policy: str = SOURCE_POLICY,
) -> QuestionVersion:
    objective_id = None
    if question.objective_index is not None and 0 <= question.objective_index < len(objectives):
        objective_id = objectives[question.objective_index].id
    family = QuestionFamily(subject_id=topic.subject_id, primary_topic_id=topic.id)
    session.add(family)
    session.flush()
    version = QuestionVersion(
        family_id=family.id,
        version=1,
        text=question.text.strip(),
        style=question.style,
        difficulty=question.difficulty,
        source_policy=source_policy,
        objective_id=objective_id,
        secondary_topic_ids="[]",
        suggested_prep_s=question.suggested_prep_s,
        suggested_answer_s=question.suggested_answer_s,
        status="active" if review_note is None else "needs_review",
        review_note=review_note,
        provenance_json=json.dumps(
            {
                "origin": "ai",
                "prompt_version": prompts.QUESTIONS_PROMPT_VERSION,
                "job_id": job.id,
                **provenance.model_dump(),
            },
            ensure_ascii=False,
        ),
    )
    session.add(version)
    session.flush()
    session.add(
        RubricVersion(question_version_id=version.id, rubric_json=question.rubric.model_dump_json())
    )
    session.flush()
    return version


def generate_summary(job: AiJob) -> dict[str, Any]:
    results = jobs.step_results(job).values()
    rejected: dict[str, int] = {}
    for result in results:
        for reason, count in result.get("rejected", {}).items():
            rejected[reason] = rejected.get(reason, 0) + count
    return {
        "batches_total": job.steps_total,
        "batches_done": len(results),
        "saved": sum(r.get("saved", 0) for r in results),
        "needs_review": sum(r.get("needs_review", 0) for r in results),
        "rejected": rejected,
    }


jobs.register_step_handler(GENERATE_KIND, generate_step)
jobs.register_summarizer(GENERATE_KIND, generate_summary)


def _get_version(session: Session, version_id: int) -> QuestionVersion:
    version = session.get(QuestionVersion, version_id)
    if version is None:
        raise HTTPException(404, QUESTION_NOT_FOUND)
    return version


def _rubric(session: Session, version_id: int) -> Rubric:
    row = session.scalar(
        select(RubricVersion).where(RubricVersion.question_version_id == version_id)
    )
    return Rubric.model_validate_json(row.rubric_json)


def question_out(version: QuestionVersion, primary_topic_id: int | None) -> QuestionOut:
    return QuestionOut(
        version_id=version.id,
        family_id=version.family_id,
        version=version.version,
        text=version.text,
        style=version.style,
        difficulty=version.difficulty,
        primary_topic_id=primary_topic_id,
        objective_id=version.objective_id,
        status=version.status,
        suggested_prep_s=version.suggested_prep_s,
        suggested_answer_s=version.suggested_answer_s,
        source_policy=version.source_policy,
    )


# --- passages across document versions ------------------------------------------------------


@dataclass
class _Lines:
    cited: dict[int, tuple[SourcePassage, int, int, str]]  # id -> (passage, version, doc, title)
    current: dict[int, int | None]  # id -> latest successor; itself when current, None removed
    reached: dict[int, int]  # id -> the version its line reaches (latest, or where it ended)


def _lines(session: Session, passage_ids: set[int]) -> _Lines:
    """Two statements whatever the number of passages and versions: the cited passages with
    their version and document, then the carried-over versions of those documents with their
    passages' predecessor links."""
    newest = aliased(DocumentVersion)
    latest_of = (
        select(func.max(newest.version))
        .where(newest.document_id == DocumentVersion.document_id)
        .correlate(DocumentVersion)
        .scalar_subquery()
    )
    rows = session.execute(
        select(
            SourcePassage,
            DocumentVersion.version,
            DocumentVersion.document_id,
            Document.title,
            latest_of,
        )  # fmt: skip
        .join(DocumentVersion, DocumentVersion.id == SourcePassage.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(SourcePassage.id.in_(passage_ids))
    ).all()
    documents = {row[2] for row in rows}
    # One row per carried-over version even when none of its passages has a predecessor (a
    # total rewrite): the version is marked explicitly, never guessed from its passages.
    links = session.execute(
        select(
            SourcePassage.id,
            SourcePassage.previous_id,
            DocumentVersion.version,
            DocumentVersion.document_id,
        )  # fmt: skip
        .select_from(DocumentVersion)
        .outerjoin(
            SourcePassage,
            (SourcePassage.document_version_id == DocumentVersion.id)
            & SourcePassage.previous_id.is_not(None),
        )
        .where(DocumentVersion.document_id.in_(documents), DocumentVersion.carried_over.is_(True))
    ).all()
    successor = {
        previous: (pid, version) for pid, previous, version, _ in links if previous is not None
    }
    linked = {(document_id, version) for _, _, version, document_id in links}
    lines = _Lines({}, {}, {})
    for passage, version, document_id, title, latest in rows:
        lines.cited[passage.id] = (passage, version, document_id, title)
        found, at = passage.id, version
        while at < latest:
            if (document_id, at + 1) not in linked:
                break  # a version not carried over (stored before it existed): stays current
            step = successor.get(found)
            if step is None:
                found, at = None, at + 1
                break
            found, at = step
        lines.current[passage.id] = found
        lines.reached[passage.id] = at
    return lines


def current_passages(session: Session, passage_ids: set[int]) -> dict[int, int | None]:
    """id -> its latest successor (itself when current, None when removed); unknown ids map to
    themselves. A constant number of statements."""
    current = _lines(session, set(passage_ids)).current
    return {pid: current.get(pid, pid) for pid in passage_ids}


def _cited_ids(rubric: Rubric) -> list[int]:
    return list(
        dict.fromkeys(
            [i for p in rubric.essential_points for i in p.passage_ids]
            + [i for p in rubric.enrichment_points for i in p.passage_ids]
            + [i for issue in rubric.source_issues for i in issue.passage_ids]
        )
    )


def _moved(rubric: Rubric, current: dict[int, int | None]) -> Rubric:
    """The rubric with every citation moved to its successor (callers refuse removed ones)."""
    moved = rubric.model_copy(deep=True)
    for item in [*moved.essential_points, *moved.enrichment_points, *moved.source_issues]:
        ids = (current.get(i, i) for i in item.passage_ids)
        item.passage_ids = list(dict.fromkeys(i for i in ids if i is not None))
    return moved


def _passage_ref(passage: SourcePassage) -> PassageRef:
    return PassageRef(
        id=passage.id, locator=passage.locator, heading=passage.heading, text=passage.text
    )


def _source_changes(session: Session, ids: list[int]) -> list[SourceChange]:
    """Cited passages changed or removed in a newer version; three statements, always."""
    lines = _lines(session, set(ids))
    finals = {c for i, c in lines.current.items() if c is not None and c != i}
    successors = {
        p.id: p for p in session.scalars(select(SourcePassage).where(SourcePassage.id.in_(finals)))
    }
    changes: list[SourceChange] = []
    for pid in ids:
        if pid not in lines.cited or lines.current[pid] == pid:
            continue
        passage, version, document_id, title = lines.cited[pid]
        new = successors.get(lines.current[pid]) if lines.current[pid] is not None else None
        if new is not None and passage_key(new.text) == passage_key(passage.text):
            continue  # unchanged text: nothing to review
        changes.append(
            SourceChange(
                document_id=document_id,
                document_title=title,
                old_version=version,
                new_version=lines.reached[pid],
                old=_passage_ref(passage),
                new=_passage_ref(new) if new is not None else None,
            )
        )
    return changes


def _origin(version: QuestionVersion) -> str:
    try:
        return str(json.loads(version.provenance_json).get("origin") or "ai")
    except (ValueError, AttributeError):
        return "ai"


def get_question(
    session: Session, version_id: int, *, with_rubric: bool, with_changes: bool = False
) -> QuestionOut | QuestionWithRubric:
    version = _get_version(session, version_id)
    family = session.get(QuestionFamily, version.family_id)
    out = question_out(version, family.primary_topic_id)
    if not with_rubric:
        return out
    rubric = _rubric(session, version.id)
    ids = _cited_ids(rubric)
    found = {
        p.id: p for p in session.scalars(select(SourcePassage).where(SourcePassage.id.in_(ids)))
    }
    passages = [
        PassageRef(id=p.id, locator=p.locator, heading=p.heading, text=p.text)
        for p in (found.get(i) for i in ids)
        if p is not None
    ]
    return QuestionWithRubric(
        **out.model_dump(),
        rubric=rubric,
        passages=passages,
        review_note=version.review_note,
        basis=validation.question_basis(rubric, version.source_policy),
        source_changes=_source_changes(session, ids) if with_changes else [],
        origin=_origin(version),
    )


def list_questions(
    session: Session,
    subject_id: int,
    *,
    topic_ids: list[int] | None = None,
    statuses: tuple[str, ...] = ("active",),
    chapter_id: int | None = None,
) -> list[QuestionOut]:
    query = (
        select(QuestionVersion, QuestionFamily.primary_topic_id)
        .join(QuestionFamily, QuestionFamily.id == QuestionVersion.family_id)
        .where(QuestionFamily.subject_id == subject_id, QuestionVersion.status.in_(statuses))
        .order_by(QuestionVersion.id)
    )
    if topic_ids is not None:
        query = query.where(QuestionFamily.primary_topic_id.in_(topic_ids))
    if chapter_id is not None:
        # A question's chapter is its primary topic's chapter (no column on the family).
        query = query.join(Topic, Topic.id == QuestionFamily.primary_topic_id).where(
            Topic.chapter_id == chapter_id
        )
    return [question_out(version, topic_id) for version, topic_id in session.execute(query)]


def edit_question(
    session: Session,
    version_id: int,
    text: str | None,
    style: str | None,
    difficulty: str | None,
    rubric: Rubric | None,
) -> QuestionWithRubric:
    """Never rewrites history: saves version + 1 with its own rubric and supersedes the old one."""
    old = _get_version(session, version_id)
    latest = session.scalar(
        select(func.max(QuestionVersion.version)).where(QuestionVersion.family_id == old.family_id)
    )
    if old.version != latest:
        raise HTTPException(409, EDIT_LATEST)
    if old.status == "retired":
        raise HTTPException(409, EDIT_RETIRED)
    family = session.get(QuestionFamily, old.family_id)
    rubric = rubric or _rubric(session, old.id)
    # Citations of an older document version move to their successor before validation. A
    # removed passage is refused wherever it is cited (essential or enrichment point, source
    # issue), never dropped silently.
    current = current_passages(session, set(_cited_ids(rubric)))
    if any(successor is None for successor in current.values()):
        raise HTTPException(422, f"{INVALID_EDIT}: {validation.REASON_REMOVED_PASSAGE}.")
    candidate = GeneratedQuestion(
        text=text if text is not None else old.text,
        style=style or old.style,
        difficulty=difficulty or old.difficulty,
        rubric=_moved(rubric, current),
        suggested_prep_s=old.suggested_prep_s,
        suggested_answer_s=old.suggested_answer_s,
    )
    topic = session.get(Topic, family.primary_topic_id) if family.primary_topic_id else None
    allowed = set()
    if topic is not None and topic.status == "active":
        allowed = allowed_passage_ids(session, topic.id)
    if not allowed:
        # A split topic is removed and has no passages left (and a topic whose passages were
        # all removed by a new document version has none either); its questions stay under it
        # for review, so any active topic's passages of the subject count as sources.
        allowed = subject_passage_ids(session, family.subject_id)
    result = validation.validate_question(candidate, allowed, [], policy=old.source_policy)
    if result.question is None:
        raise HTTPException(422, f"{INVALID_EDIT}: {result.reason}.")
    edited = result.question
    provenance = json.loads(old.provenance_json)
    new = QuestionVersion(
        family_id=old.family_id,
        version=old.version + 1,
        text=edited.text,
        style=edited.style,
        difficulty=edited.difficulty,
        source_policy=old.source_policy,
        objective_id=old.objective_id,
        secondary_topic_ids=old.secondary_topic_ids,
        suggested_prep_s=edited.suggested_prep_s,
        suggested_answer_s=edited.suggested_answer_s,
        status="active",
        provenance_json=json.dumps(
            {
                "origin": "user",
                "edited_from": old.id,
                "prompt_version": provenance.get("prompt_version"),
            },
            ensure_ascii=False,
        ),
    )
    old.status = "superseded"
    session.add(new)
    session.flush()
    session.add(
        RubricVersion(question_version_id=new.id, rubric_json=edited.rubric.model_dump_json())
    )
    session.commit()
    return get_question(session, new.id, with_rubric=True, with_changes=True)


def keep_question(session: Session, version_id: int) -> QuestionWithRubric:
    """ "Keep (still correct)": version + 1 with the same text and rubric, its citations moved
    to the latest version's passages; never rewrites the old version's rows."""
    old = _get_version(session, version_id)
    latest = session.scalar(
        select(func.max(QuestionVersion.version)).where(QuestionVersion.family_id == old.family_id)
    )
    if old.version != latest:
        raise HTTPException(409, EDIT_LATEST)
    if old.status == "retired":
        raise HTTPException(409, EDIT_RETIRED)
    rubric = _rubric(session, old.id)
    current = current_passages(session, set(_cited_ids(rubric)))
    if any(successor is None for successor in current.values()):
        raise HTTPException(409, KEEP_REMOVED)
    if all(successor == pid for pid, successor in current.items()):
        raise HTTPException(409, KEEP_NOT_NEEDED)
    new = QuestionVersion(
        family_id=old.family_id,
        version=old.version + 1,
        text=old.text,
        style=old.style,
        difficulty=old.difficulty,
        source_policy=old.source_policy,
        objective_id=old.objective_id,
        secondary_topic_ids=old.secondary_topic_ids,
        suggested_prep_s=old.suggested_prep_s,
        suggested_answer_s=old.suggested_answer_s,
        status="active",
        provenance_json=json.dumps({"origin": "kept", "kept_from": old.id}),
    )
    old.status = "superseded"
    session.add(new)
    session.flush()
    session.add(
        RubricVersion(
            question_version_id=new.id, rubric_json=_moved(rubric, current).model_dump_json()
        )
    )
    session.commit()
    return get_question(session, new.id, with_rubric=True, with_changes=True)


def check_question(session: Session, version_id: int) -> QuestionWithRubric:
    """ "Mark as checked": lifts the review hold in place. Text, rubric and version stay."""
    version = _get_version(session, version_id)
    latest = session.scalar(
        select(func.max(QuestionVersion.version)).where(
            QuestionVersion.family_id == version.family_id
        )
    )
    if version.version != latest:
        raise HTTPException(409, EDIT_LATEST)
    if version.status == "retired":
        raise HTTPException(409, EDIT_RETIRED)
    if version.status != "needs_review":
        raise HTTPException(409, NOT_IN_REVIEW)
    ids = _cited_ids(_rubric(session, version.id))
    if any(successor is None for successor in current_passages(session, set(ids)).values()):
        raise HTTPException(409, KEEP_REMOVED)
    # Same test as the card's source changes: a passage carried over with unchanged text needs
    # no review, so only a changed one sends the learner to Keep.
    if _source_changes(session, ids):
        raise HTTPException(409, CHECK_USE_KEEP)
    version.status = "active"
    version.review_note = None
    session.commit()
    return get_question(session, version.id, with_rubric=True, with_changes=True)


def retire_question(session: Session, version_id: int) -> QuestionOut:
    version = _get_version(session, version_id)
    version.status = "retired"
    session.commit()
    return get_question(session, version.id, with_rubric=False)
