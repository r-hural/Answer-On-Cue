from fastapi import APIRouter

from app.features.system.version import app_version

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": "answeroncue", "version": app_version()}
