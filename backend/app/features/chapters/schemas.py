from pydantic import BaseModel


class ChapterOut(BaseModel):
    id: int
    subject_id: int
    name: str | None  # None only for the default chapter while unnamed
    is_default: bool
    ordinal: int
    document_count: int
    topic_count: int  # active topics
    question_count: int  # active + needs_review versions of its topics' families


class ChapterIn(BaseModel):
    name: str


class ChapterPatch(BaseModel):
    name: str


class MoveDocumentIn(BaseModel):
    chapter_id: int | None = None
    new_chapter_name: str | None = None  # exactly one of the two


class MoveResult(BaseModel):
    document_id: int
    chapter_id: int
    topics_moved: list[int]  # active topic ids now in the target chapter
