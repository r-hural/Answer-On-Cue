"""Shared builders for the topics/questions tests (no tests here)."""

import re
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.features.library.models import Document, DocumentVersion, SourcePassage, Subject
from app.features.topics.prompts import (
    ConsolidatedTopic,
    Consolidation,
    SectionTerm,
    SectionTopic,
    SectionTopics,
)

BACKEND = Path(__file__).resolve().parent.parent
PASSAGE_RE = re.compile(r'<passage id="(\d+)">(.*?)</passage>', re.DOTALL)
REF_RE = re.compile(r"\[(s\d+t\d+)\]")


def upgrade(url: str) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()
    command.upgrade(cfg, "head")


def make_subject(session: Session, name: str = "Historia") -> Subject:
    subject = Subject(name=name)
    session.add(subject)
    session.flush()
    return subject


def make_version(
    session: Session,
    subject_id: int,
    count: int = 30,
    size: int = 900,
    excluded: tuple[int, ...] = (5, 17),
    status: str = "imported",
) -> tuple[DocumentVersion, list[SourcePassage]]:
    document = Document(subject_id=subject_id, title="Podręcznik")
    session.add(document)
    session.flush()
    version = DocumentVersion(
        document_id=document.id,
        version=1,
        filename="a.txt",
        stored_path="documents/a.txt",
        mime="text/plain",
        size_bytes=1,
        sha256="0" * 64,
        status=status,
        extracted_text="x",
        char_count=count * size,
    )
    session.add(version)
    session.flush()
    passages = []
    for i in range(count):
        text = (f"Fragment {i}: ateńska demokracja i polis. " * 40)[:size]
        passage = SourcePassage(
            document_version_id=version.id,
            ordinal=i,
            heading=f"Rozdział {i // 10 + 1}",
            locator=f"sekcja {i + 1}",
            text=text,
            char_start=i * size,
            char_end=(i + 1) * size,
            excluded=i in excluded,
        )
        session.add(passage)
        passages.append(passage)
    session.commit()
    return version, passages


def passage_ids_in(messages) -> list[int]:
    return [int(m) for msg in messages for m, _ in PASSAGE_RE.findall(msg.content)]


def passage_chars_in(messages) -> int:
    return sum(len(text) for msg in messages for _, text in PASSAGE_RE.findall(msg.content))


def fake_section_topics(messages, extra_ids: tuple[int, ...] = ()) -> SectionTopics:
    """Three topics per section, each citing a third of the section's passages."""
    ids = passage_ids_in(messages)
    thirds = [ids[i::3] for i in range(3)]
    return SectionTopics(
        topics=[
            SectionTopic(
                title=f"Temat {ids[0]}-{n}",
                description=f"Opis tematu {ids[0]}-{n}",
                objectives=[f"Wyjaśnić zagadnienie {ids[0]}-{n}"],
                passage_ids=[*part, *extra_ids],
                terms=[SectionTerm(term=f"Pojęcie {ids[0]}-{n}", definition="Definicja")],
            )
            for n, part in enumerate(thirds)
            if part
        ]
    )


def fake_consolidation(messages) -> Consolidation:
    """Merges section topics pairwise in the order listed."""
    refs = [r for msg in messages for r in REF_RE.findall(msg.content)]
    pairs = [refs[i : i + 2] for i in range(0, len(refs), 2)]
    return Consolidation(
        topics=[
            ConsolidatedTopic(title=f"Scalony {n}", description="Opis", merged_from=pair)
            for n, pair in enumerate(pairs)
        ]
    )


def make_topic(
    session: Session,
    subject_id: int,
    title: str,
    passages: list[SourcePassage] = (),
    objectives: tuple[str, ...] = (),
    terms: tuple[str, ...] = (),
    ordinal: int = 0,
):
    from app.features.topics.models import LearningObjective, Term, Topic, TopicPassage

    topic = Topic(subject_id=subject_id, title=title, ordinal=ordinal, origin="ai", status="active")
    session.add(topic)
    session.flush()
    for n, text in enumerate(objectives):
        session.add(LearningObjective(topic_id=topic.id, text=text, ordinal=n))
    for passage in passages:
        session.add(TopicPassage(topic_id=topic.id, passage_id=passage.id))
    for term in terms:
        session.add(
            Term(subject_id=subject_id, topic_id=topic.id, term=term, term_key=term.casefold())
        )
    session.commit()
    return topic


def make_question(
    session: Session,
    subject_id: int,
    topic_id: int | None,
    text: str = "Omów ustrój ateńskiej demokracji.",
    status: str = "active",
    passage_ids: tuple[int, ...] = (),
):
    import json

    from app.features.questions.models import QuestionFamily, QuestionVersion, RubricVersion

    family = QuestionFamily(subject_id=subject_id, primary_topic_id=topic_id)
    session.add(family)
    session.flush()
    question = QuestionVersion(
        family_id=family.id,
        version=1,
        text=text,
        style="open",
        difficulty="recall",
        source_policy="materials",
        secondary_topic_ids="[]",
        suggested_prep_s=30,
        suggested_answer_s=120,
        status=status,
        provenance_json=json.dumps({"origin": "ai"}),
    )
    session.add(question)
    session.flush()
    rubric = {
        "essential_points": [
            {
                "id": "p1",
                "text": "Zgromadzenie ludowe",
                "weight": 0.5,
                "passage_ids": list(passage_ids),
            },
            {"id": "p2", "text": "Rada Pięciuset", "weight": 0.5, "passage_ids": list(passage_ids)},
        ],
        "model_answer": "Demokracja ateńska opierała się na zgromadzeniu i radzie.",
    }
    session.add(RubricVersion(question_version_id=question.id, rubric_json=json.dumps(rubric)))
    session.commit()
    return question
