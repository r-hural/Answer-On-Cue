from pydantic import BaseModel, Field


class ExtractIn(BaseModel):
    document_version_id: int


class ObjectiveOut(BaseModel):
    id: int
    text: str


class TopicOut(BaseModel):
    id: int
    title: str
    description: str | None
    origin: str
    status: str
    objectives: list[ObjectiveOut]
    passage_count: int
    question_count: int
    children: list["TopicOut"] = Field(default_factory=list)
    # the same count per source policy ("materials", "expanded", "outside"), every key present
    question_counts: dict[str, int] = Field(default_factory=dict)
    chapter_id: int | None = None


class TermOut(BaseModel):
    term: str
    definition: str | None
    topic_id: int | None


class TopicPatch(BaseModel):
    title: str | None = None
    description: str | None = None


class MergeIn(BaseModel):
    topic_ids: list[int] = Field(min_length=2)
    title: str
    description: str | None = None


class SplitPart(BaseModel):
    title: str
    objective_ids: list[int] = Field(default_factory=list)
    passage_ids: list[int] = Field(default_factory=list)


class SplitIn(BaseModel):
    parts: list[SplitPart] = Field(min_length=2)


class TopicDeleted(BaseModel):
    topic_id: int
    questions_retired: int
