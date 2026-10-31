from collections.abc import Callable
from typing import Annotated, Any, Literal

import anthropic
import httpx2
import openai
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.features.providers.errors import MESSAGES, map_sdk_error
from app.features.providers.keys import (
    AZURE_REGION_ENV_NAME,
    KEY_ENV_NAMES,
    mask,
    remove_env_value,
    set_env_value,
)
from app.features.providers.registry import missing_key_message, resolve_role
from app.features.providers.types import ProviderError, ProviderName, register_secret
from app.features.voice.azure_adapter import endpoint, headers, status_error_kind
from app.local_requests import require_local_request

router = APIRouter(prefix="/providers", tags=["providers"])

# Tests replace this to send the key test through a MockTransport.
http_client_factory: Callable[[], httpx2.Client | None] = lambda: None  # noqa: E731

KEY_TEST_TIMEOUT = 30.0
INVALID_KEY = "Invalid key."
MISSING_REGION = "Enter the Azure Speech region."
KEY_OK = "The key works."

SettingsDep = Annotated[Settings, Depends(get_settings)]
ProviderParam = Literal["openai", "anthropic", "azure_speech"]


class KeyStatus(BaseModel):
    provider: ProviderName
    configured: bool
    masked: str | None
    region: str | None


class RoleStatus(BaseModel):
    provider: str
    model: str


class ProvidersStatus(BaseModel):
    keys: list[KeyStatus]
    roles: dict[str, RoleStatus]


class KeyTestResult(BaseModel):
    ok: bool
    error_kind: str | None
    message: str


def _key_value(settings: Settings, provider: ProviderName) -> str | None:
    secret = {
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
        "azure_speech": settings.azure_speech_key,
    }[provider]
    value = secret.get_secret_value().strip() if secret is not None else ""
    return value or None


def build_status(settings: Settings) -> ProvidersStatus:
    keys = []
    for provider in KEY_ENV_NAMES:
        value = _key_value(settings, provider)
        region = settings.azure_speech_region if provider == "azure_speech" else None
        keys.append(
            KeyStatus(
                provider=provider,
                configured=value is not None,
                masked=mask(value) if value else None,
                region=(region or None) if value else None,
            )
        )
    roles = {}
    for role in ("transcription", "generation", "assessment"):
        provider, model = resolve_role(settings, role)
        roles[role] = RoleStatus(provider=provider, model=model)
    return ProvidersStatus(keys=keys, roles=roles)


def _reload(request: Request) -> Settings:
    from app.main import reload_settings

    return reload_settings(request.app)


def _clean_token(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    # Keys go into HTTP headers: printable ASCII only, so a pasted curly quote is caught here.
    if not value or not value.isascii() or not value.isprintable() or " " in value:
        return None
    return value


@router.get("/status")
def status(settings: SettingsDep) -> ProvidersStatus:
    return build_status(settings)


@router.put("/keys/{provider}", dependencies=[Depends(require_local_request)])
def put_key(
    provider: ProviderParam,
    request: Request,
    settings: SettingsDep,
    body: Annotated[Any, Body()] = None,
) -> ProvidersStatus:
    # The body is checked by hand: a pydantic 422 would echo the submitted key back.
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail=INVALID_KEY)
    value = _clean_token(body.get("value"))
    if value is None:
        raise HTTPException(status_code=422, detail=INVALID_KEY)
    region = None
    if provider == "azure_speech":
        region = _clean_token(body.get("region"))
        if region is None:
            raise HTTPException(status_code=422, detail=MISSING_REGION)
    register_secret(value)
    set_env_value(settings.user_env_file, KEY_ENV_NAMES[provider], value)
    if region is not None:
        set_env_value(settings.user_env_file, AZURE_REGION_ENV_NAME, region)
    return build_status(_reload(request))


@router.delete("/keys/{provider}", dependencies=[Depends(require_local_request)])
def delete_key(provider: ProviderParam, request: Request, settings: SettingsDep) -> ProvidersStatus:
    names = [KEY_ENV_NAMES[provider]]
    if provider == "azure_speech":
        names.append(AZURE_REGION_ENV_NAME)
    # The developer file too, or the key would come back from it after the reload.
    for path in (settings.user_env_file, settings.base_env_file):
        if path is not None and path.is_file():
            for name in names:
                remove_env_value(path, name)
    return build_status(_reload(request))


def _test_azure_key(settings: Settings, value: str) -> KeyTestResult:
    """Lists the region's voices: needs a valid key and region, costs nothing."""
    region = (settings.azure_speech_region or "").strip()
    if not region:
        return KeyTestResult(ok=False, error_kind="unavailable", message=MISSING_REGION)
    try:
        url = endpoint(region, "cognitiveservices/voices/list")
    except ProviderError as error:
        return KeyTestResult(ok=False, error_kind=error.kind, message=error.message)
    injected = http_client_factory()
    client = injected or httpx2.Client(timeout=KEY_TEST_TIMEOUT)
    try:
        response = client.get(url, headers=headers(value), timeout=KEY_TEST_TIMEOUT)
    except httpx2.TimeoutException:
        return KeyTestResult(ok=False, error_kind="timeout", message=MESSAGES["timeout"])
    except httpx2.TransportError:
        return KeyTestResult(ok=False, error_kind="network", message=MESSAGES["network"])
    finally:
        if injected is None:
            client.close()
    if response.status_code >= 400:
        kind = status_error_kind(response.status_code)
        return KeyTestResult(ok=False, error_kind=kind, message=MESSAGES[kind])
    return KeyTestResult(ok=True, error_kind=None, message=KEY_OK)


@router.post("/keys/{provider}/test")
def test_key(provider: ProviderParam, settings: SettingsDep) -> KeyTestResult:
    value = _key_value(settings, provider)
    if value is None:
        return KeyTestResult(
            ok=False, error_kind="unavailable", message=missing_key_message(provider)
        )
    register_secret(value)
    if provider == "azure_speech":
        return _test_azure_key(settings, value)
    sdk = openai if provider == "openai" else anthropic
    try:
        if provider == "openai":
            client = openai.OpenAI(
                api_key=value,
                max_retries=0,
                timeout=KEY_TEST_TIMEOUT,
                http_client=http_client_factory(),
            )
            client.models.list()
        else:
            client = anthropic.Anthropic(
                api_key=value,
                max_retries=0,
                timeout=KEY_TEST_TIMEOUT,
                http_client=http_client_factory(),
            )
            client.models.list(limit=1)
    except (openai.OpenAIError, anthropic.AnthropicError) as exc:
        error = map_sdk_error(sdk, provider, exc)
        return KeyTestResult(ok=False, error_kind=error.kind, message=MESSAGES[error.kind])
    return KeyTestResult(ok=True, error_kind=None, message=KEY_OK)
