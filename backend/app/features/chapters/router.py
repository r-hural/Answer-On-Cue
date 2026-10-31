from fastapi import APIRouter, Response

from app.db.session import SessionDep
from app.features.chapters import service
from app.features.chapters.schemas import (
    ChapterIn,
    ChapterOut,
    ChapterPatch,
    MoveDocumentIn,
    MoveResult,
)

router = APIRouter()


@router.get("/subjects/{subject_id}/chapters")
def list_chapters(subject_id: int, session: SessionDep) -> list[ChapterOut]:
    return service.list_chapters(session, subject_id)


@router.post("/subjects/{subject_id}/chapters", status_code=201)
def create_chapter(subject_id: int, body: ChapterIn, session: SessionDep) -> ChapterOut:
    return service.create_chapter(session, subject_id, body.name)


@router.patch("/chapters/{chapter_id}")
def rename_chapter(chapter_id: int, body: ChapterPatch, session: SessionDep) -> ChapterOut:
    return service.rename_chapter(session, chapter_id, body.name)


@router.delete("/chapters/{chapter_id}", status_code=204)
def delete_chapter(chapter_id: int, session: SessionDep) -> Response:
    service.delete_chapter(session, chapter_id)
    return Response(status_code=204)


@router.post("/documents/{document_id}/chapter")
def move_document(document_id: int, body: MoveDocumentIn, session: SessionDep) -> MoveResult:
    return service.move_document(session, document_id, body)
