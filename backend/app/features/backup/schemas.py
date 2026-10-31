from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Counts(BaseModel):
    subjects: int = 0
    documents: int = 0
    document_versions: int = 0
    topics: int = 0
    questions: int = 0
    rounds: int = 0
    answers: int = 0
    recordings: int = 0


class StorageOut(BaseModel):
    data_dir: str
    total_bytes: int
    database_bytes: int
    documents_bytes: int
    recordings_bytes: int
    recordings: int
    recordings_deleted: int
    audio_retention: str
    pre_restore_keep: int = 5
    export_keep_days: int = 30


class BackupOut(BaseModel):
    name: str
    bytes: int
    created_at: datetime
    counts: Counts


class RestorePreview(BaseModel):
    token: str
    created_at: datetime
    app_version: str
    counts: Counts
    current: Counts
    newer_rounds: int  # rounds on this computer started after the backup was made


class RestoreResult(BaseModel):
    restored: Counts
    pre_restore_backup: str


class ExportOut(BaseModel):
    name: str
    bytes: int
    rounds: int
    answers: int


class AudioDeletionPreview(BaseModel):
    recordings: int
    bytes: int
    kept_unresolved: int
    fingerprint: str


class AudioDeletionResult(BaseModel):
    deleted: int
    bytes: int
    skipped_in_use: int


Policy = Literal["forever", "assessed", "30d", "90d"]


class RetentionIn(BaseModel):
    policy: Policy
    fingerprint: str | None = None


class AudioDeleteIn(BaseModel):
    scope: Literal["older_than", "all", "subject"]
    days: int | None = None
    subject_id: int | None = None
    fingerprint: str


class CleanupIn(BaseModel):
    pre_restore_keep: int
    export_keep_days: int


class CleanupOut(BaseModel):
    pre_restore_keep: int
    export_keep_days: int
    deleted_backups: int
    deleted_exports: int
