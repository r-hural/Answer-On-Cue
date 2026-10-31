"""Topics: AI extraction in sections (a resumable job), the topic tree, terms and glossary."""

from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.features.chapters import service as chapters
from app.features.jobs import service as jobs
from app.features.jobs.models import AiJob
from app.features.languages.service import profile_for_subject
from app.features.library import service as library
from app.features.library.models import Document, DocumentVersion, SourcePassage
from app.features.providers.base import TextGenerator
from app.features.providers.types import Message
from app.features.questions.models import QuestionFamily, QuestionVersion
from app.features.topics import prompts
from app.features.topics.models import LearningObjective, Term, Topic, TopicPassage
from app.features.topics.schemas import ObjectiveOut, SplitPart, TermOut, TopicDeleted, TopicOut

EXTRACT_KIND = "topics.extract"
SECTION_CHAR_LIMIT = 12000
MAX_OUTPUT_TOKENS = 8000
TITLE_LIMIT = 200
TERM_LIMIT = 200

NOT_IMPORTED = "The document was not imported correctly."
ALREADY_RUNNING = "This document is already being analysed."
NO_PASSAGES = "The document has no passages to analyse."
TOPIC_NOT_FOUND = "Topic not found."
TOPIC_REMOVED = "This topic has been removed."
INVALID_TITLE = "Enter a topic name (up to 200 characters)."
MERGE_INVALID = "Choose at least two different active topics of the same subject."
SPLIT_INCOMPLETE = "Every objective and passage must go to exactly one part."
SPLIT_NOTE = "topic split"
CHAPTER_MISMATCH = "Topics from different chapters cannot be merged. Move one first."
LIVE_QUESTION_STATUSES = ("active", "needs_review")
COUNTED_QUESTION_STATUSES = ("active", "needs_review")
NOTHING_NEW = (
    "This version has no new passages to analyse: its topics came over from the previous version."
)
VERSION_REPLACED = "A newer version of this document exists. Use the latest version."


def compute_sections(passages: list[SourcePassage]) -> list[list[int]]:
    """Consecutive non-excluded passages, cut before the one that would pass the limit."""
    sections: list[list[int]] = []
    current: list[int] = []
    size = 0
    for passage in sorted(passages, key=lambda p: p.ordinal):
        if passage.excluded:
            continue
        length = len(passage.text)
        if current and size + length > SECTION_CHAR_LIMIT:
            sections.append(current)
            current, size = [], 0
        current.append(passage.id)
        size += length
    if current:
        sections.append(current)
    return sections


def refuse_replaced(session: Session, version: DocumentVersion) -> None:
    """Older versions are read-only: only the latest one is analysed or generated from."""
    latest = session.scalar(
        select(func.max(DocumentVersion.version)).where(
            DocumentVersion.document_id == version.document_id
        )
    )
    if version.version != latest:
        raise HTTPException(409, VERSION_REPLACED)


def finished_before(session: Session, version: DocumentVersion, kind: str) -> int | None:
    """The newest earlier version number of the document with a finished job of `kind`."""
    if version.version <= 1:
        return None
    return session.scalar(
        select(func.max(DocumentVersion.version))
        .join(AiJob, AiJob.document_version_id == DocumentVersion.id)
        .where(
            DocumentVersion.document_id == version.document_id,
            DocumentVersion.version < version.version,
            AiJob.kind == kind,
            AiJob.status == "done",
        )
    )


def ancestors_at(session: Session, version: DocumentVersion, base: int) -> dict[int, int | None]:
    """Each passage of the version -> its ancestor in version `base` or earlier (the first one
    met walking the previous_id chain back), None when its line starts after `base` (added in
    this version or in an intermediate one). One statement, whatever the number of versions."""
    rows = session.execute(
        select(SourcePassage.id, SourcePassage.previous_id, DocumentVersion.version)
        .join(DocumentVersion, DocumentVersion.id == SourcePassage.document_version_id)
        .where(
            DocumentVersion.document_id == version.document_id,
            DocumentVersion.version <= version.version,
        )
    ).all()
    line = {pid: (previous, number) for pid, previous, number in rows}
    found: dict[int, int | None] = {}
    for pid, (previous, number) in line.items():
        if number != version.version:
            continue
        step, seen = previous, set()
        while step in line and line[step][1] > base and step not in seen:
            seen.add(step)
            step = line[step][0]
        found[pid] = step if step in line else None
    return found


def _only_new_passages(session: Session, version: DocumentVersion) -> int | None:
    """A later version whose document had a finished extraction sends only the passages added
    since the newest analysed version; returns that version number (None: send everything)."""
    return finished_before(session, version, EXTRACT_KIND)


def extraction_sections(session: Session, version: DocumentVersion) -> list[list[int]]:
    """The sections start_extraction sends: every usable passage, or only those added since the
    newest analysed earlier version (also in a version replaced before it was analysed)."""
    passages = list(version.passages)
    analysed = _only_new_passages(session, version)
    if analysed is not None:
        ancestors = ancestors_at(session, version, analysed)
        passages = [p for p in passages if ancestors.get(p.id) is None]
    return compute_sections(passages)


def start_extraction(session: Session, document_version_id: int) -> AiJob:
    version = library.get_version(session, document_version_id)
    if version.status != "imported":
        raise HTTPException(409, NOT_IMPORTED)
    refuse_replaced(session, version)
    if jobs.find_live_job(session, EXTRACT_KIND, version.id) is not None:
        raise HTTPException(409, ALREADY_RUNNING)
    # Sections are fixed at start, so a resume works on the same split.
    sections = extraction_sections(session, version)
    if not sections:
        nothing_new = _only_new_passages(session, version) is not None
        raise HTTPException(409, NOTHING_NEW if nothing_new else NO_PASSAGES)
    document = session.get(Document, version.document_id)
    if document.chapter_id is None:
        document.chapter_id = chapters.ensure_default_chapter(session, document.subject_id)
    return jobs.create_job(
        session,
        EXTRACT_KIND,
        document.subject_id,
        {
            "document_version_id": version.id,
            "sections": sections,
            "chapter_id": document.chapter_id,
        },
        steps_total=len(sections) + 1,
        document_version_id=version.id,
    )


def usable_passages(
    session: Session, version_id: int, ids: list[int] | None = None
) -> dict[int, SourcePassage]:
    query = select(SourcePassage).where(
        SourcePassage.document_version_id == version_id, SourcePassage.excluded.is_(False)
    )
    if ids is not None:
        query = query.where(SourcePassage.id.in_(ids))
    return {p.id: p for p in session.scalars(query.order_by(SourcePassage.ordinal))}


def operation_id(job: AiJob, index: int) -> str:
    return f"{job.kind}:{job.id}:{index}"


def extract_step(
    session: Session, job: AiJob, index: int, generator: TextGenerator
) -> dict[str, Any]:
    params = jobs.load_params(job)
    sections: list[list[int]] = params["sections"]
    version_id = params["document_version_id"]
    if index < len(sections):
        return _extract_section(session, job, index, version_id, sections[index], generator)
    chapter_id = params.get("chapter_id") or _version_chapter(session, job.subject_id, version_id)
    return _consolidate_and_save(session, job, index, version_id, chapter_id, generator)


def _version_chapter(session: Session, subject_id: int, version_id: int) -> int:
    """A job started before chapters existed: the version's document's current chapter."""
    chapter_id = session.scalar(
        select(Document.chapter_id)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(DocumentVersion.id == version_id)
    )
    return chapter_id or chapters.ensure_default_chapter(session, subject_id)


def _extract_section(
    session: Session,
    job: AiJob,
    index: int,
    version_id: int,
    section_ids: list[int],
    generator: TextGenerator,
) -> dict[str, Any]:
    passages = usable_passages(session, version_id, section_ids)
    if not passages:
        return {"topics": []}
    profile = profile_for_subject(session, job.subject_id)
    message = prompts.extract_section_message(
        [(p.id, p.text) for p in passages.values()], profile=profile
    )
    result = generator.generate_structured(
        "topics.extract_section",
        prompts.extract_section_system(profile=profile),
        [Message(role="user", content=message)],
        prompts.SectionTopics,
        operation_id=operation_id(job, index),
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    topics = []
    for topic in result.value.topics:
        if not topic.title.strip():
            continue
        topic.passage_ids = [pid for pid in dict.fromkeys(topic.passage_ids) if pid in passages]
        topics.append(topic.model_dump())
    return {
        "topics": topics,
        "prompt_version": prompts.TOPICS_PROMPT_VERSION,
        "provenance": result.provenance.model_dump(),
    }


def _merge(group: list[dict[str, Any]], title: str, description: str | None) -> dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "objectives": list(dict.fromkeys(o for t in group for o in t["objectives"] if o.strip())),
        "passage_ids": list(dict.fromkeys(pid for t in group for pid in t["passage_ids"])),
        "terms": [term for t in group for term in t["terms"]],
    }


def _existing_ref(ref: str, existing: dict[int, Topic]) -> int | None:
    """The id of an existing chapter topic named by an [e…] reference, else None."""
    ref = ref.strip().strip("[]")
    if not ref.startswith("e") or not ref[1:].isdigit():
        return None
    topic_id = int(ref[1:])
    return topic_id if topic_id in existing else None


def _consolidate_and_save(
    session: Session,
    job: AiJob,
    index: int,
    version_id: int,
    chapter_id: int,
    generator: TextGenerator,
) -> dict[str, Any]:
    results = jobs.step_results(job)
    section_topics: dict[str, dict[str, Any]] = {
        f"s{s}t{t}": topic
        for s in range(index)
        for t, topic in enumerate(results.get(s, {}).get("topics", []))
    }
    # Topics merge only within a chapter: the chapter's active topics may be extended, no other.
    existing = {
        t.id: t
        for t in session.scalars(
            select(Topic)
            .where(
                Topic.subject_id == job.subject_id,
                Topic.chapter_id == chapter_id,
                Topic.status == "active",
            )
            .order_by(Topic.ordinal, Topic.id)
        )
    }
    extensions: list[tuple[Topic, dict[str, Any]]] = []
    if section_topics and (index > 1 or existing):
        listing = [(ref, t["title"], t["description"]) for ref, t in section_topics.items()]
        profile = profile_for_subject(session, job.subject_id)
        if existing:
            system = prompts.consolidate_chapter_system(profile=profile)
            message = prompts.consolidate_chapter_message(
                listing,
                [(f"e{t.id}", t.title, t.description or "") for t in existing.values()],
                profile=profile,
            )
        else:
            system = prompts.consolidate_system(profile=profile)
            message = prompts.consolidate_message(listing, profile=profile)
        result = generator.generate_structured(
            "topics.consolidate",
            system,
            [Message(role="user", content=message)],
            prompts.Consolidation,
            operation_id=operation_id(job, index),
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        used: set[str] = set()
        final: list[dict[str, Any]] = []
        for merged in result.value.topics:
            refs = [r for r in merged.merged_from if r in section_topics and r not in used]
            if not refs or not merged.title.strip():
                continue
            used.update(refs)
            group = _merge([section_topics[r] for r in refs], merged.title, merged.description)
            # Only the first valid [e…] reference counts; existing topics never merge together.
            target = next(
                (i for r in merged.merged_from if (i := _existing_ref(r, existing)) is not None),
                None,
            )
            if target is not None:
                extensions.append((existing[target], group))
            else:
                final.append(group)
        # A section topic the model left out is kept on its own, so no passage loses its topic.
        for ref, topic in section_topics.items():
            if ref not in used:
                final.append(_merge([topic], topic["title"], topic["description"]))
    else:
        final = [_merge([t], t["title"], t["description"]) for t in section_topics.values()]

    terms_created = _save_topics(session, job.subject_id, version_id, final, chapter_id)
    existing_terms = _subject_terms(session, job.subject_id)
    for topic, data in extensions:
        terms_created += _extend_topic(session, topic, data, version_id, existing_terms)
    session.flush()
    return {
        "topics_created": len(final),
        "terms_created": terms_created,
        "topics_extended": len({topic.id for topic, _ in extensions}),
    }


def _subject_terms(session: Session, subject_id: int) -> dict[str, Term]:
    return {
        t.term_key: t for t in session.scalars(select(Term).where(Term.subject_id == subject_id))
    }


def _add_terms(
    session: Session,
    subject_id: int,
    topic_id: int,
    terms: list[dict[str, Any]],
    existing_terms: dict[str, Term],
) -> int:
    created = 0
    for term_data in terms:
        term = term_data["term"].strip()[:TERM_LIMIT]
        key = term.casefold()
        if not key:
            continue
        existing = existing_terms.get(key)
        if existing is not None:
            if existing.topic_id is None:
                existing.topic_id = topic_id
            continue
        existing_terms[key] = Term(
            subject_id=subject_id,
            topic_id=topic_id,
            term=term,
            term_key=key,
            definition=(term_data.get("definition") or "").strip() or None,
        )
        session.add(existing_terms[key])
        created += 1
    return created


def _save_topics(
    session: Session,
    subject_id: int,
    version_id: int,
    topics: list[dict[str, Any]],
    chapter_id: int | None = None,
) -> int:
    valid = set(usable_passages(session, version_id))
    document_id = _version_document(session, version_id)
    ordinal = session.scalar(select(func.max(Topic.ordinal)).where(Topic.subject_id == subject_id))
    ordinal = -1 if ordinal is None else ordinal
    existing_terms = _subject_terms(session, subject_id)
    terms_created = 0
    for data in topics:
        ordinal += 1
        topic = Topic(
            subject_id=subject_id,
            title=data["title"].strip()[:TITLE_LIMIT],
            description=(data["description"] or "").strip() or None,
            ordinal=ordinal,
            origin="ai",
            status="active",
            chapter_id=chapter_id,
        )
        session.add(topic)
        session.flush()
        for n, text in enumerate(data["objectives"]):
            session.add(
                LearningObjective(
                    topic_id=topic.id, text=text.strip(), ordinal=n, document_id=document_id
                )
            )
        for pid in data["passage_ids"]:
            if pid in valid:
                session.add(TopicPassage(topic_id=topic.id, passage_id=pid))
        terms_created += _add_terms(session, subject_id, topic.id, data["terms"], existing_terms)
    session.flush()
    return terms_created


def _version_document(session: Session, version_id: int) -> int | None:
    return session.scalar(
        select(DocumentVersion.document_id).where(DocumentVersion.id == version_id)
    )


def _extend_topic(
    session: Session,
    topic: Topic,
    data: dict[str, Any],
    version_id: int,
    existing_terms: dict[str, Term],
) -> int:
    """Adds the new passages, objectives (deduplicated, appended) and terms to an existing
    topic; its title and description stay, since they may be the user's edits."""
    valid = set(usable_passages(session, version_id))
    linked = set(
        session.scalars(select(TopicPassage.passage_id).where(TopicPassage.topic_id == topic.id))
    )
    for pid in data["passage_ids"]:
        if pid in valid and pid not in linked:
            session.add(TopicPassage(topic_id=topic.id, passage_id=pid))
            linked.add(pid)
    objectives = list(
        session.scalars(select(LearningObjective).where(LearningObjective.topic_id == topic.id))
    )
    document_id = _version_document(session, version_id)
    known = {o.text.strip().casefold(): o for o in objectives}
    ordinal = max((o.ordinal for o in objectives), default=-1)
    for text in data["objectives"]:
        found = known.get(text.strip().casefold())
        if found is not None:
            if found.document_id is not None and found.document_id != document_id:
                found.document_id = None  # the same objective in another book: shared
            continue
        ordinal += 1
        known[text.strip().casefold()] = objective = LearningObjective(
            topic_id=topic.id, text=text.strip(), ordinal=ordinal, document_id=document_id
        )
        session.add(objective)
    return _add_terms(session, topic.subject_id, topic.id, data["terms"], existing_terms)


def extract_summary(job: AiJob) -> dict[str, Any]:
    sections_total = len(jobs.load_params(job).get("sections", []))
    results = jobs.step_results(job)
    summary = {
        "sections_total": sections_total,
        "sections_done": sum(1 for i in results if i < sections_total),
        "topics_created": results.get(sections_total, {}).get("topics_created", 0),
    }
    extended = results.get(sections_total, {}).get("topics_extended", 0)
    if extended:  # appended only when a chapter topic was extended
        summary["topics_extended"] = extended
    return summary


jobs.register_step_handler(EXTRACT_KIND, extract_step)
jobs.register_summarizer(EXTRACT_KIND, extract_summary)


def get_topic(session: Session, topic_id: int) -> Topic:
    topic = session.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(404, TOPIC_NOT_FOUND)
    return topic


def topic_tree(
    session: Session,
    subject_id: int,
    include_removed: bool = False,
    chapter_id: int | None = None,
) -> list[TopicOut]:
    library.get_subject(session, subject_id)
    query = select(Topic).where(Topic.subject_id == subject_id)
    if chapter_id is not None:
        query = query.where(Topic.chapter_id == chapter_id)
    if not include_removed:
        query = query.where(Topic.status == "active")
    topics = list(session.scalars(query.order_by(Topic.ordinal, Topic.id)))
    nodes = topics_out(session, topics)
    roots: list[TopicOut] = []
    for topic in topics:
        parent = nodes.get(topic.parent_id) if topic.parent_id else None
        (parent.children if parent else roots).append(nodes[topic.id])
    return roots


def topics_out(session: Session, topics: list[Topic]) -> dict[int, TopicOut]:
    """Flat TopicOut per topic id, with objectives and counts; children are left empty."""
    ids = [t.id for t in topics]
    objectives: dict[int, list[ObjectiveOut]] = {i: [] for i in ids}
    for objective in session.scalars(
        select(LearningObjective)
        .where(LearningObjective.topic_id.in_(ids))
        .order_by(LearningObjective.ordinal, LearningObjective.id)
    ):
        objectives[objective.topic_id].append(ObjectiveOut(id=objective.id, text=objective.text))
    passage_counts = dict(
        session.execute(
            select(TopicPassage.topic_id, func.count())
            .where(TopicPassage.topic_id.in_(ids))
            .group_by(TopicPassage.topic_id)
        ).all()
    )
    by_policy: dict[int, dict[str, int]] = {
        i: {"materials": 0, "expanded": 0, "outside": 0} for i in ids
    }
    for topic_id, policy, count in session.execute(
        select(QuestionFamily.primary_topic_id, QuestionVersion.source_policy, func.count())
        .join(QuestionVersion, QuestionVersion.family_id == QuestionFamily.id)
        .where(
            QuestionFamily.primary_topic_id.in_(ids),
            QuestionVersion.status.in_(COUNTED_QUESTION_STATUSES),
        )
        .group_by(QuestionFamily.primary_topic_id, QuestionVersion.source_policy)
    ):
        by_policy[topic_id][policy] = by_policy[topic_id].get(policy, 0) + count
    return {
        t.id: TopicOut(
            id=t.id,
            title=t.title,
            description=t.description,
            origin=t.origin,
            status=t.status,
            objectives=objectives[t.id],
            passage_count=passage_counts.get(t.id, 0),
            question_count=sum(by_policy[t.id].values()),
            question_counts=by_policy[t.id],
            chapter_id=t.chapter_id,
        )
        for t in topics
    }


def topic_out(session: Session, topic: Topic) -> TopicOut:
    return topics_out(session, [topic])[topic.id]


def list_terms(session: Session, subject_id: int) -> list[TermOut]:
    library.get_subject(session, subject_id)
    terms = session.scalars(
        select(Term).where(Term.subject_id == subject_id).order_by(Term.term_key)
    )
    return [TermOut(term=t.term, definition=t.definition, topic_id=t.topic_id) for t in terms]


def get_glossary(
    session: Session, subject_id: int, topic_ids: list[int] | None = None, limit: int = 60
) -> list[str]:
    """Terms of active topics (optionally only the given ones), for transcription prompts."""
    query = (
        select(Term.term)
        .join(Topic, Topic.id == Term.topic_id)
        .where(Term.subject_id == subject_id, Topic.status == "active")
        .order_by(Topic.ordinal, Term.id)
        .limit(limit)
    )
    if topic_ids is not None:
        query = query.where(Term.topic_id.in_(topic_ids))
    return list(session.scalars(query))


def _clean_title(title: str | None) -> str:
    title = (title or "").strip()
    if not title or len(title) > TITLE_LIMIT:
        raise HTTPException(422, INVALID_TITLE)
    return title


def _active_topic(session: Session, topic_id: int) -> Topic:
    topic = get_topic(session, topic_id)
    if topic.status != "active":
        raise HTTPException(409, TOPIC_REMOVED)
    return topic


def _family_ids(session: Session, topic_id: int) -> list[int]:
    return list(
        session.scalars(
            select(QuestionFamily.id).where(QuestionFamily.primary_topic_id == topic_id)
        )
    )


def _live_questions(session: Session, topic_id: int) -> list[QuestionVersion]:
    return list(
        session.scalars(
            select(QuestionVersion).where(
                QuestionVersion.family_id.in_(_family_ids(session, topic_id)),
                QuestionVersion.status.in_(LIVE_QUESTION_STATUSES),
            )
        )
    )


def update_topic(
    session: Session,
    topic_id: int,
    title: str | None,
    description: str | None,
    fields: set[str],
) -> TopicOut:
    topic = _active_topic(session, topic_id)
    if "title" in fields:
        topic.title = _clean_title(title)
    if "description" in fields:
        topic.description = (description or "").strip() or None
    topic.origin = "user"
    session.commit()
    return topic_out(session, topic)


def merge_topics(
    session: Session, topic_ids: list[int], title: str, description: str | None
) -> TopicOut:
    title = _clean_title(title)
    ids = list(dict.fromkeys(topic_ids))
    sources = [get_topic(session, i) for i in ids]
    if (
        len(sources) < 2
        or any(t.status != "active" for t in sources)
        or len({t.subject_id for t in sources}) != 1
    ):
        raise HTTPException(422, MERGE_INVALID)
    if len({t.chapter_id for t in sources}) != 1:
        raise HTTPException(422, CHAPTER_MISMATCH)
    merged = Topic(
        subject_id=sources[0].subject_id,
        chapter_id=sources[0].chapter_id,
        title=title,
        description=(description or "").strip() or sources[0].description,
        ordinal=min(t.ordinal for t in sources),
        origin="user",
        status="active",
    )
    session.add(merged)
    session.flush()

    objectives = session.scalars(
        select(LearningObjective)
        .where(LearningObjective.topic_id.in_(ids))
        .order_by(LearningObjective.topic_id, LearningObjective.ordinal)
    )
    objectives_by_topic = sorted(objectives, key=lambda o: (ids.index(o.topic_id), o.ordinal))
    for n, objective in enumerate(objectives_by_topic):
        objective.topic_id, objective.ordinal = merged.id, n
    _move_passages(session, ids, {merged.id: None})
    for term in session.scalars(select(Term).where(Term.topic_id.in_(ids))):
        term.topic_id = merged.id
    for family in session.scalars(
        select(QuestionFamily).where(QuestionFamily.primary_topic_id.in_(ids))
    ):
        family.primary_topic_id = merged.id
    for source in sources:
        source.status = "removed"
    session.commit()
    return topic_out(session, merged)


def _move_passages(
    session: Session, from_ids: list[int], targets: dict[int, set[int] | None]
) -> None:
    """Re-links passages of from_ids: to every target with None, or to the one listing the id."""
    links = list(session.scalars(select(TopicPassage).where(TopicPassage.topic_id.in_(from_ids))))
    passage_ids = list(dict.fromkeys(link.passage_id for link in links))
    for link in links:
        session.delete(link)
    session.flush()
    for target, wanted in targets.items():
        for pid in passage_ids:
            if wanted is None or pid in wanted:
                session.add(TopicPassage(topic_id=target, passage_id=pid))


def split_topic(session: Session, topic_id: int, parts: list[SplitPart]) -> list[TopicOut]:
    original = _active_topic(session, topic_id)
    titles = [_clean_title(part.title) for part in parts]
    objectives = {
        o.id: o
        for o in session.scalars(
            select(LearningObjective).where(LearningObjective.topic_id == original.id)
        )
    }
    passage_ids = set(
        session.scalars(select(TopicPassage.passage_id).where(TopicPassage.topic_id == original.id))
    )
    assigned_objectives = [oid for part in parts for oid in part.objective_ids]
    assigned_passages = [pid for part in parts for pid in part.passage_ids]
    # No part listing a passage means every part keeps all of them.
    passages_ok = not assigned_passages or sorted(assigned_passages) == sorted(passage_ids)
    if sorted(assigned_objectives) != sorted(objectives) or not passages_ok:
        raise HTTPException(422, SPLIT_INCOMPLETE)

    new_topics: list[Topic] = []
    for title, part in zip(titles, parts, strict=True):
        topic = Topic(
            subject_id=original.subject_id,
            chapter_id=original.chapter_id,
            parent_id=original.parent_id,
            title=title,
            ordinal=original.ordinal,
            origin="user",
            status="active",
        )
        session.add(topic)
        session.flush()
        new_topics.append(topic)
        for n, oid in enumerate(sorted(part.objective_ids, key=lambda i: objectives[i].ordinal)):
            objectives[oid].topic_id, objectives[oid].ordinal = topic.id, n
    _move_passages(
        session,
        [original.id],
        {
            t.id: (set(p.passage_ids) if assigned_passages else None)
            for t, p in zip(new_topics, parts, strict=True)
        },
    )
    # Terms are not assigned per part; they follow the first part so the glossary keeps them.
    for term in session.scalars(select(Term).where(Term.topic_id == original.id)):
        term.topic_id = new_topics[0].id
    part_of_objective = {
        oid: topic.id
        for topic, part in zip(new_topics, parts, strict=True)
        for oid in part.objective_ids
    }
    # Each family follows the part holding its newest live question's objective.
    live_questions = _live_questions(session, original.id)
    for family_id in _family_ids(session, original.id):
        live = list(
            session.scalars(
                select(QuestionVersion)
                .where(
                    QuestionVersion.family_id == family_id,
                    QuestionVersion.status.in_(LIVE_QUESTION_STATUSES),
                )
                .order_by(QuestionVersion.version.desc())
            )
        )
        if not live:
            continue
        family = session.get(QuestionFamily, family_id)
        family.primary_topic_id = part_of_objective.get(live[0].objective_id, new_topics[0].id)
    for question in live_questions:
        question.status = "needs_review"
        question.review_note = SPLIT_NOTE
    original.status = "removed"
    session.commit()
    nodes = topics_out(session, new_topics)
    return [nodes[t.id] for t in new_topics]


def remove_topic(session: Session, topic_id: int) -> TopicDeleted:
    topic = _active_topic(session, topic_id)
    questions = _live_questions(session, topic.id)
    for question in questions:
        question.status = "retired"
    topic.status = "removed"
    session.commit()
    return TopicDeleted(topic_id=topic.id, questions_retired=len(questions))
