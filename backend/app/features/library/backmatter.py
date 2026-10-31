"""Back matter of a document (footnotes, bibliography, links), suggested for exclusion.

The rule never excludes on its own: the user decides, and can undo it.
"""

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.library import service
from app.features.library.models import SourcePassage

# Wikipedia print-outs end with footnotes, bibliography and links. The passage holding the
# first marker still starts with real content, so only the passages after it are suggested.
BACK_MATTER_MARKERS = ("Przypisy", "Bibliografia", "Linki zewnętrzne")
# The other languages' headings match a whole line only (after strip, a trailing ":" and
# casefold): words like "References", "Notes" or "Literatur" also occur inside sentences.
BACK_MATTER_HEADINGS = (
    "References", "Bibliography", "Notes", "Footnotes", "External links", "Further reading",
    "Literatur", "Einzelnachweise", "Anmerkungen", "Weblinks", "Quellen",
    "Bibliographie", "Références", "Notes et références", "Liens externes",
    "Bibliografía", "Referencias", "Notas", "Enlaces externos",
    "Note", "Collegamenti esterni", "Riferimenti",  # it ("Bibliografia" is a Polish marker)
    "Література", "Примітки", "Джерела", "Посилання",
)  # fmt: skip
_HEADING_KEYS = frozenset(heading.casefold() for heading in BACK_MATTER_HEADINGS)

FOREIGN_PASSAGES = "Some passages do not belong to this version of the document."


class BackMatterSuggestion(BaseModel):
    passage_ids: list[int]
    count: int
    first_locator: str | None
    last_locator: str | None
    already_excluded: bool  # every suggested passage is already excluded


class ExclusionIn(BaseModel):
    passage_ids: list[int]
    excluded: bool


class ExclusionOut(BaseModel):
    updated: int


def _starts_back_matter(text: str) -> bool:
    if any(marker in text for marker in BACK_MATTER_MARKERS):
        return True
    return any(
        line.strip().rstrip(":").strip().casefold() in _HEADING_KEYS for line in text.splitlines()
    )


def back_matter(passages: list[SourcePassage]) -> list[SourcePassage]:
    ordered = sorted(passages, key=lambda p: p.ordinal)
    for index, passage in enumerate(ordered):
        if _starts_back_matter(passage.text):
            return ordered[index + 1 :]
    return []


def suggestion(session: Session, version_id: int) -> BackMatterSuggestion:
    version = service.get_version(session, version_id)
    found = back_matter(list(version.passages))
    return BackMatterSuggestion(
        passage_ids=[p.id for p in found],
        count=len(found),
        first_locator=found[0].locator if found else None,
        last_locator=found[-1].locator if found else None,
        already_excluded=bool(found) and all(p.excluded for p in found),
    )


def set_exclusion(
    session: Session, version_id: int, passage_ids: list[int], excluded: bool
) -> ExclusionOut:
    service.get_version(session, version_id)
    ids = set(passage_ids)
    rows = session.scalars(
        select(SourcePassage).where(
            SourcePassage.document_version_id == version_id, SourcePassage.id.in_(ids)
        )
    ).all()
    if len(rows) != len(ids):
        raise HTTPException(422, FOREIGN_PASSAGES)
    for passage in rows:
        passage.excluded = excluded
    session.commit()
    return ExclusionOut(updated=len(rows))
