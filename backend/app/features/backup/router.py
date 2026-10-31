from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.config import Settings, get_settings
from app.db.session import SessionDep
from app.features.backup import archive, cleanup, export, storage
from app.features.backup.guards import BACKUP_NAME, EXPORT_NAME, require_local_host, stored_file
from app.features.backup.schemas import (
    AudioDeleteIn,
    AudioDeletionPreview,
    AudioDeletionResult,
    BackupOut,
    CleanupIn,
    CleanupOut,
    ExportOut,
    Policy,
    RestorePreview,
    RestoreResult,
    RetentionIn,
    StorageOut,
)
from app.features.library.models import Subject
from app.features.providers.keys import set_env_value
from app.features.recording import retention
from app.local_requests import require_local_request

router = APIRouter(prefix="/data", tags=["backup"])

SettingsDep = Annotated[Settings, Depends(get_settings)]

SELECTION_CHANGED = "The recordings changed since you looked. Check the new numbers."
DAYS_REQUIRED = "Enter the number of days."
SUBJECT_REQUIRED = "Choose a subject."
SUBJECT_NOT_FOUND = "Subject not found."
BACKUPS_RANGE = "Enter a number of backups from 0 to 100."
EXPORT_DAYS_RANGE = "Enter a number of days from 0 to 3650."


def _preview(selection: retention.AudioSelection) -> AudioDeletionPreview:
    return AudioDeletionPreview(
        recordings=len(selection.recording_ids),
        bytes=selection.bytes,
        kept_unresolved=selection.kept_unresolved,
        fingerprint=retention.fingerprint(selection.recording_ids),
    )


def _changed(selection: retention.AudioSelection) -> HTTPException:
    return HTTPException(
        409, {"message": SELECTION_CHANGED, "preview": _preview(selection).model_dump()}
    )


def _result(done: tuple[int, int, int]) -> AudioDeletionResult:
    deleted, size, skipped = done
    return AudioDeletionResult(deleted=deleted, bytes=size, skipped_in_use=skipped)


def _prune(settings: Settings) -> cleanup.Pruned:
    return cleanup.prune(settings, now=storage.utcnow())


def _checked_scope(session, scope: str, days: int | None, subject_id: int | None) -> None:
    if scope == "older_than" and (days is None or days < 0):
        raise HTTPException(422, DAYS_REQUIRED)
    if scope == "subject":
        if subject_id is None:
            raise HTTPException(422, SUBJECT_REQUIRED)
        if session.scalar(select(Subject.id).where(Subject.id == subject_id)) is None:
            raise HTTPException(404, SUBJECT_NOT_FOUND)


@router.get("/storage", dependencies=[Depends(require_local_host)])
def get_storage(session: SessionDep, settings: SettingsDep) -> StorageOut:
    return storage.storage(session, settings)


@router.get("/retention/preview")
def retention_preview(policy: Policy, session: SessionDep) -> AudioDeletionPreview:
    return _preview(
        retention.select_recordings(session, scope="policy", policy=policy, now=storage.utcnow())
    )


@router.put("/retention", dependencies=[Depends(require_local_request)])
def put_retention(
    body: RetentionIn, request: Request, session: SessionDep, settings: SettingsDep
) -> AudioDeletionResult:
    from app.main import reload_settings

    now = storage.utcnow()
    selection = retention.select_recordings(session, scope="policy", policy=body.policy, now=now)
    if body.fingerprint is not None and body.fingerprint != retention.fingerprint(
        selection.recording_ids
    ):
        raise _changed(selection)
    set_env_value(settings.user_env_file, "AOC_AUDIO_RETENTION", body.policy)
    reload_settings(request.app)
    return _result(retention.delete_audio(session, settings, selection.recording_ids, now))


@router.put("/cleanup", dependencies=[Depends(require_local_request)])
def put_cleanup(body: CleanupIn, request: Request, settings: SettingsDep) -> CleanupOut:
    from app.main import reload_settings

    if not 0 <= body.pre_restore_keep <= 100:
        raise HTTPException(422, BACKUPS_RANGE)
    if not 0 <= body.export_keep_days <= 3650:
        raise HTTPException(422, EXPORT_DAYS_RANGE)
    set_env_value(settings.user_env_file, "AOC_PRE_RESTORE_KEEP", str(body.pre_restore_keep))
    set_env_value(settings.user_env_file, "AOC_EXPORT_KEEP_DAYS", str(body.export_keep_days))
    pruned = _prune(reload_settings(request.app))
    return CleanupOut(
        pre_restore_keep=body.pre_restore_keep,
        export_keep_days=body.export_keep_days,
        deleted_backups=len(pruned.backups),
        deleted_exports=len(pruned.exports),
    )


@router.get("/recordings/deletion-preview")
def recordings_preview(
    session: SessionDep,
    scope: Literal["older_than", "all", "subject"],
    days: Annotated[int | None, Query(ge=0)] = None,
    subject_id: int | None = None,
) -> AudioDeletionPreview:
    _checked_scope(session, scope, days, subject_id)
    return _preview(
        retention.select_recordings(
            session, scope=scope, days=days, subject_id=subject_id, now=storage.utcnow()
        )
    )


@router.post("/recordings/delete", dependencies=[Depends(require_local_request)])
def delete_recordings(
    body: AudioDeleteIn, session: SessionDep, settings: SettingsDep
) -> AudioDeletionResult:
    _checked_scope(session, body.scope, body.days, body.subject_id)
    now = storage.utcnow()
    selection = retention.select_recordings(
        session, scope=body.scope, days=body.days, subject_id=body.subject_id, now=now
    )
    if body.fingerprint != retention.fingerprint(selection.recording_ids):
        raise _changed(selection)
    return _result(retention.delete_audio(session, settings, selection.recording_ids, now))


@router.post("/backups", status_code=201, dependencies=[Depends(require_local_request)])
def make_backup(session: SessionDep, settings: SettingsDep) -> BackupOut:
    backup = archive.create_backup(session, settings, now=storage.utcnow())
    _prune(settings)
    return backup


@router.get("/backups/{name}", dependencies=[Depends(require_local_host)])
def download_backup(name: str, settings: SettingsDep) -> FileResponse:
    path = stored_file(settings.data_dir / "backups", name, BACKUP_NAME)
    return FileResponse(path, media_type="application/zip", filename=name)


@router.post("/restore", dependencies=[Depends(require_local_request)])
def upload_restore(
    file: Annotated[UploadFile, File()], session: SessionDep, settings: SettingsDep
) -> RestorePreview:
    return archive.stage_upload(session, settings, file.file)


@router.post("/restore/{token}/apply", dependencies=[Depends(require_local_request)])
def apply_restore(token: str, session: SessionDep, settings: SettingsDep) -> RestoreResult:
    try:
        return archive.apply_restore(session, settings, token)
    finally:
        _prune(settings)  # after the restore lock is released, so its pre-restore backup counts


@router.delete("/restore/{token}", status_code=204, dependencies=[Depends(require_local_request)])
def discard_restore(token: str, settings: SettingsDep) -> Response:
    archive.discard_staged(settings, token)
    return Response(status_code=204)


@router.post("/exports", status_code=201, dependencies=[Depends(require_local_request)])
def make_export(session: SessionDep, settings: SettingsDep) -> ExportOut:
    made = export.create_export(session, settings, now=storage.utcnow())
    _prune(settings)
    return made


@router.get("/exports/{name}", dependencies=[Depends(require_local_host)])
def download_export(name: str, settings: SettingsDep) -> FileResponse:
    path = stored_file(settings.data_dir / "exports", name, EXPORT_NAME)
    return FileResponse(path, media_type="application/zip", filename=name)
