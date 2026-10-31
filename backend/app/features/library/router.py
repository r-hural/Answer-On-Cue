from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)

from app.config import Settings, get_settings
from app.db.session import SessionDep
from app.features.languages.service import LANGUAGE_LOCKED
from app.features.library import backmatter, service, versions
from app.features.library.backmatter import BackMatterSuggestion, ExclusionIn, ExclusionOut
from app.features.library.schemas import (
    DeletionPreview,
    DocumentDetailOut,
    DocumentOut,
    PassageOut,
    PassagePage,
    PassagePatch,
    PreviewOut,
    SubjectIn,
    SubjectOut,
    SubjectPatch,
    VersionApplied,
    VersionApplyIn,
    VersionChange,
)
from app.local_requests import require_local_request

router = APIRouter()

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def refuse_language_change(request: Request) -> None:
    """Runs before body validation, so the learner gets the message, not a pydantic list."""
    try:
        body = await request.json()
    except ValueError:
        return
    if isinstance(body, dict) and "language" in body:
        raise HTTPException(422, LANGUAGE_LOCKED)


@router.get("/subjects")
def list_subjects(session: SessionDep) -> list[SubjectOut]:
    return service.list_subjects(session)


@router.post("/subjects", status_code=201)
def create_subject(body: SubjectIn, session: SessionDep) -> SubjectOut:
    return service.create_subject(session, body.name, body.description, body.language)


@router.get("/subjects/{subject_id}")
def get_subject(subject_id: int, session: SessionDep) -> SubjectOut:
    return service.subject_out(session, service.get_subject(session, subject_id))


@router.patch("/subjects/{subject_id}", dependencies=[Depends(refuse_language_change)])
def update_subject(subject_id: int, body: SubjectPatch, session: SessionDep) -> SubjectOut:
    return service.update_subject(
        session, subject_id, body.name, body.description, body.model_fields_set
    )


@router.get("/subjects/{subject_id}/deletion-preview")
def subject_deletion_preview(
    subject_id: int, session: SessionDep, settings: SettingsDep
) -> DeletionPreview:
    subject = service.get_subject(session, subject_id)
    return service.deletion_preview(session, settings, subject)


@router.delete("/subjects/{subject_id}", dependencies=[Depends(require_local_request)])
def delete_subject(
    subject_id: int,
    session: SessionDep,
    settings: SettingsDep,
    confirm: bool = False,
    fingerprint: str | None = None,
) -> DeletionPreview:
    return service.delete_subject(session, settings, subject_id, confirm, fingerprint)


@router.post("/subjects/{subject_id}/documents", status_code=201)
def upload_document(
    subject_id: int,
    session: SessionDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    chapter_id: Annotated[int | None, Form()] = None,
    chapter_name: Annotated[str | None, Form()] = None,
) -> DocumentOut:
    data = service.read_upload(file.file)
    return service.create_document(
        session,
        settings,
        subject_id,
        file.filename or "",
        data,
        title,
        chapter_id=chapter_id,
        chapter_name=chapter_name,
    )


@router.get("/subjects/{subject_id}/documents")
def list_documents(subject_id: int, session: SessionDep) -> list[DocumentOut]:
    return service.list_documents(session, subject_id)


@router.get("/documents/{document_id}")
def get_document(document_id: int, session: SessionDep) -> DocumentDetailOut:
    return service.get_document_detail(session, document_id)


@router.post("/documents/{document_id}/versions", status_code=201)
def upload_version(
    document_id: int,
    session: SessionDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
) -> DocumentOut:
    service.get_document(session, document_id)
    data = service.read_upload(file.file)
    return service.add_version(session, settings, document_id, file.filename or "", data)


@router.post("/documents/{document_id}/versions/preview")
def preview_version(
    document_id: int,
    session: SessionDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
) -> VersionChange:
    service.get_document(session, document_id)
    data = service.read_upload(file.file)
    return versions.preview_version(session, settings, document_id, file.filename or "", data)


@router.post("/documents/{document_id}/versions/{token}/apply", status_code=201)
def apply_version(
    document_id: int, token: str, body: VersionApplyIn, session: SessionDep, settings: SettingsDep
) -> VersionApplied:
    return versions.apply_version(session, settings, document_id, token, body.fingerprint)


@router.delete("/documents/{document_id}/versions/{token}", status_code=204)
def discard_version(
    document_id: int, token: str, session: SessionDep, settings: SettingsDep
) -> Response:
    service.get_document(session, document_id)
    versions.discard_version(settings, document_id, token)
    return Response(status_code=204)


@router.get("/document-versions/{version_id}/passages")
def list_passages(
    version_id: int,
    session: SessionDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PassagePage:
    return service.list_passages(session, version_id, offset, limit)


@router.get("/document-versions/{version_id}/preview")
def version_preview(version_id: int, session: SessionDep) -> PreviewOut:
    return service.version_preview(session, version_id)


@router.get("/document-versions/{version_id}/back-matter")
def version_back_matter(version_id: int, session: SessionDep) -> BackMatterSuggestion:
    return backmatter.suggestion(session, version_id)


@router.post("/document-versions/{version_id}/passages/exclusion")
def exclude_passages(version_id: int, body: ExclusionIn, session: SessionDep) -> ExclusionOut:
    return backmatter.set_exclusion(session, version_id, body.passage_ids, body.excluded)


@router.patch("/passages/{passage_id}")
def patch_passage(passage_id: int, body: PassagePatch, session: SessionDep) -> PassageOut:
    return service.set_passage_excluded(session, passage_id, body.excluded)
