from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.features.languages.profiles import LanguageCode


class SubjectIn(BaseModel):
    name: str
    description: str | None = None
    language: LanguageCode


class SubjectPatch(BaseModel):
    # The language is locked after creation (refused by the router with LANGUAGE_LOCKED).
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None


class SubjectOut(BaseModel):
    id: int
    name: str
    description: str | None
    document_count: int
    created_at: datetime
    updated_at: datetime
    chapter_count: int = 1
    # needs_review questions whose note starts with "source changed: " (a replaced document)
    source_review_count: int = 0
    language: str


class DeletionPreview(BaseModel):
    subject: str
    documents: int
    versions: int
    passages: int
    files: int
    bytes: int
    topics: int = 0
    objectives: int = 0
    questions: int = 0
    question_versions: int = 0
    rounds: int = 0
    answers: int = 0
    recordings: int = 0
    recording_bytes: int = 0
    fingerprint: str = ""


class VersionOut(BaseModel):
    id: int
    version: int
    filename: str
    mime: str
    size_bytes: int
    status: str
    message: str | None
    char_count: int
    page_count: int | None
    passage_count: int
    created_at: datetime
    # updated_at of the newest done topics.extract / questions.generate job of this version
    topics_extracted_at: datetime | None = None
    questions_generated_at: datetime | None = None
    topic_count: int = 0  # active topics with a non-excluded passage of this version
    # answered attempts and rounds whose question versions cite this version (detail only)
    answer_count: int = 0
    round_count: int = 0


class DocumentOut(BaseModel):
    id: int
    title: str
    latest_version: VersionOut
    version_count: int
    chapter_id: int | None = None


class DocumentDetailOut(DocumentOut):
    versions: list[VersionOut]


class PassageOut(BaseModel):
    id: int
    ordinal: int
    heading: str | None
    locator: str
    text: str
    excluded: bool


class PassagePage(BaseModel):
    total: int
    items: list[PassageOut]


class PassagePatch(BaseModel):
    excluded: bool


class PreviewOut(BaseModel):
    status: str
    message: str | None
    char_count: int
    page_count: int | None
    text: str


class VersionChange(BaseModel):
    """What replacing a document with the staged file would change; nothing stored yet."""

    token: str
    document_id: int
    filename: str
    current_version: int
    new_version: int
    status: str
    message: str | None
    page_count: int | None
    passage_count: int
    unchanged: int
    changed: int
    removed: int
    new: int
    exclusions_carried: int
    exclusions_total: int
    questions_to_review: int
    topics_without_material: int
    fingerprint: str


class VersionApplyIn(BaseModel):
    fingerprint: str


class VersionApplied(BaseModel):
    document: DocumentOut
    questions_to_review: int
    topics_relinked: int
