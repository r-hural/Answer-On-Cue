from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.features.providers.types import ProviderError
from app.features.voice import downloads, service
from app.features.voice.cache import is_valid_key
from app.features.voice.download import DownloadError
from app.features.voice.downloads import DownloadState
from app.features.voice.service import (
    InvalidRequest,
    InvalidText,
    NotVerbatim,
    SpeakResult,
    VoiceInfo,
    VoiceProvider,
)

router = APIRouter(prefix="/voice", tags=["voice"])

SettingsDep = Annotated[Settings, Depends(get_settings)]
Rate = Annotated[float | None, Field(ge=0.5, le=1.5)]

# A missing engine or a bad Azure region is a setup problem (503); a busy provider 429.
_ERROR_STATUS = {"unavailable": 503, "bad_request": 503, "rate_limit": 429, "spending_limit": 409}


class SpeakRequest(BaseModel):
    # No length limit here: the check after normalising gives the message.
    text: str = ""
    provider: VoiceProvider | None = None
    voice: str | None = None
    rate: Rate = None
    language: str | None = None  # the content language; old rounds send none


class PreviewRequest(BaseModel):
    provider: VoiceProvider
    voice: str | None = None
    rate: Rate = None
    language: str | None = None


class SpeakResponse(BaseModel):
    provider: VoiceProvider
    voice: str
    rate: float
    audio_url: str | None
    cached: bool
    duration_ms: int | None


def _response(result: SpeakResult) -> SpeakResponse:
    return SpeakResponse(
        provider=result.provider,
        voice=result.voice,
        rate=result.rate,
        audio_url=f"/api/voice/cache/{result.cache_key}.wav" if result.cache_key else None,
        cached=result.cached,
        duration_ms=result.duration_ms,
    )


def _run(call) -> SpeakResponse:
    try:
        return _response(call())
    except (InvalidText, InvalidRequest) as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except NotVerbatim as error:
        # The question is shown as text; the exam's voice note shows this message.
        raise HTTPException(status_code=409, detail=error.message) from None
    except ProviderError as error:
        raise HTTPException(
            status_code=_ERROR_STATUS.get(error.kind, 502), detail=error.message
        ) from None


@router.get("/voices")
def voices(
    settings: SettingsDep, language: str | None = None, refresh: bool = False
) -> list[VoiceInfo]:
    """refresh=true fetches the Piper and Azure lists first when their cache is a week old."""
    try:
        return service.list_voices(settings, language, refresh=refresh)
    except InvalidRequest as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


class DownloadRequest(BaseModel):
    voice: str


@router.post("/downloads", status_code=202)
def start_download(body: DownloadRequest, settings: SettingsDep) -> DownloadState:
    try:
        return downloads.start(settings, body.voice)
    except DownloadError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@router.get("/downloads/{voice}")
def download_state(voice: str, settings: SettingsDep) -> DownloadState:
    return downloads.get_state(settings, voice)


@router.post("/speak")
def speak(body: SpeakRequest, settings: SettingsDep) -> SpeakResponse:
    return _run(
        lambda: service.speak(
            settings,
            body.text,
            language=body.language,
            provider=body.provider,
            voice=body.voice,
            rate=body.rate,
        )
    )


@router.post("/preview")
def preview(body: PreviewRequest, settings: SettingsDep) -> SpeakResponse:
    return _run(
        lambda: service.preview(
            settings, body.provider, body.voice, body.rate, language=body.language
        )
    )


@router.get("/cache/{cache_key}.wav", response_class=FileResponse)
def cached_audio(cache_key: str, settings: SettingsDep) -> FileResponse:
    if not is_valid_key(cache_key):
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        path = service.cache_file(settings, cache_key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Not Found") from None
    return FileResponse(path, media_type="audio/wav")
