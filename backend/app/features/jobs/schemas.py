from datetime import datetime
from typing import Any

from pydantic import BaseModel


class JobUsage(BaseModel):
    provider: str
    model: str
    calls: int
    errors: int
    input_tokens: int
    output_tokens: int


class JobOut(BaseModel):
    id: int
    kind: str
    status: str
    steps_total: int
    steps_done: int
    error_message: str | None
    summary: dict[str, Any]
    subject_id: int
    document_version_id: int | None
    resumable: bool  # failed, partial, or cut off by a restart
    usage: list[JobUsage]  # empty after a restart: the recorder lives in memory
    created_at: datetime
    updated_at: datetime
    subject_name: str
    document_title: str | None  # the document of document_version_id
    chapter_id: int | None = None  # per-chapter generation, or the document's chapter


class JobStarted(BaseModel):
    job_id: int
