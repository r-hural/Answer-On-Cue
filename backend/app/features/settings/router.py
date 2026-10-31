import json
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.features.providers.keys import set_env_value
from app.features.settings.schemas import Preferences, PreferencesPatch
from app.local_requests import require_local_request

router = APIRouter(prefix="/settings", tags=["settings"])

SettingsDep = Annotated[Settings, Depends(get_settings)]

BAD_MODEL = "Invalid model name."
BAD_VOICE = "Invalid voice name."
MODEL_FIELDS = ("transcription_model", "generation_model", "assessment_model")


# No "$": the dotenv reader expands ${NAME}, which would turn a name into a stored key.
NAME_PATTERN = re.compile(r"[A-Za-z0-9._:/-]{1,100}")


def _printable_ascii(value: str) -> bool:
    return NAME_PATTERN.fullmatch(value) is not None


def _preferences(settings: Settings) -> Preferences:
    return Preferences(**{name: getattr(settings, name) for name in Preferences.model_fields})


def _env_text(value: Any) -> str:
    if isinstance(value, tuple):
        return json.dumps(list(value), separators=(",", ":"))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _validation_message(error: ValidationError) -> str:
    message = str(error.errors()[0]["msg"])
    return message.removeprefix("Value error, ")


@router.get("")
def get_preferences(settings: SettingsDep) -> Preferences:
    return _preferences(settings)


@router.put("", dependencies=[Depends(require_local_request)])
def put_preferences(
    patch: PreferencesPatch, request: Request, settings: SettingsDep
) -> Preferences:
    from app.main import reload_settings

    values = patch.model_dump(exclude_unset=True, exclude_none=True)
    for name in MODEL_FIELDS:
        if name in values and not _printable_ascii(values[name]):
            raise HTTPException(status_code=422, detail=BAD_MODEL)
    if "voice_name" in values and not _printable_ascii(values["voice_name"]):
        raise HTTPException(status_code=422, detail=BAD_VOICE)
    try:
        # Validated before anything is written, so a bad value leaves the file untouched.
        validated = Settings(_env_file=None, base_env_file=None, **values)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=_validation_message(error)) from error
    for name in values:
        set_env_value(
            settings.user_env_file, f"AOC_{name.upper()}", _env_text(getattr(validated, name))
        )
    return _preferences(reload_settings(request.app))
