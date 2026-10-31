from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from fastapi.responses import FileResponse

from app.config import Settings, get_settings
from app.db.session import SessionDep
from app.features.recording import service
from app.features.recording.schemas import RecordingOut, VadResult

router = APIRouter()

SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.post("/recordings", status_code=201)
def upload_recording(
    session: SessionDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
    purpose: Annotated[str, Form()],
) -> RecordingOut:
    data = service.read_upload(file.file)
    return service.create_recording(session, settings, purpose, data)


@router.get("/recordings/{recording_id}/audio")
def get_audio(recording_id: int, session: SessionDep, settings: SettingsDep) -> FileResponse:
    recording = service.get_recording(session, recording_id)
    return FileResponse(service.audio_path(settings, recording), media_type="audio/wav")


@router.post("/recordings/{recording_id}/analyze")
def analyze_recording(recording_id: int, session: SessionDep, settings: SettingsDep) -> VadResult:
    return service.analyze_recording(session, settings, recording_id)


@router.delete("/recordings/{recording_id}", status_code=204)
def delete_recording(recording_id: int, session: SessionDep, settings: SettingsDep) -> Response:
    service.delete_recording(session, settings, recording_id)
    return Response(status_code=204)
