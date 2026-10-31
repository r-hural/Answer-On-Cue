from fastapi import APIRouter, Depends

from app.db.session import SessionDep
from app.features.languages import service
from app.features.languages.schemas import LanguageOut, LanguagePatch
from app.local_requests import require_local_request

router = APIRouter(prefix="/languages", tags=["languages"])


@router.get("")
def list_languages(session: SessionDep) -> list[LanguageOut]:
    return service.list_languages(session)


@router.put("/{code}", dependencies=[Depends(require_local_request)])
def update_language(code: str, body: LanguagePatch, session: SessionDep) -> LanguageOut:
    return service.update_language(session, code, body)
