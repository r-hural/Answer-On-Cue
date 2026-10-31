"""Azure Speech text-to-speech over REST (SSML in, RIFF/WAV out)."""

import re
import time
from collections.abc import Callable
from xml.sax.saxutils import escape, quoteattr

import httpx2
from pydantic import SecretStr

from app.features.providers import limits
from app.features.providers.errors import MESSAGES
from app.features.providers.types import (
    AudioResult,
    ErrorKind,
    Provenance,
    ProviderError,
    Usage,
    VoiceSpec,
    register_secret,
)
from app.features.voice.cache import wav_duration_ms

OUTPUT_FORMAT = "riff-24khz-16bit-mono-pcm"
USER_AGENT = "answeroncue"
TIMEOUT = 30.0
MAX_RETRIES = limits.SDK_MAX_RETRIES
MAX_RETRY_AFTER = 5.0
BAD_REGION_MESSAGE = "Invalid Azure region."
UNKNOWN_AZURE_VOICE = "Unknown Azure voice."
REGION_PATTERN = re.compile(r"^[a-z0-9]+$")
SSML_NAMESPACE = "http://www.w3.org/2001/10/synthesis"
# The xx-YY (or xx-Script-YY) locale that starts a voice name such as de-DE-KatjaNeural.
_VOICE_LOCALE = re.compile(r"([a-z]{2,3}(?:-[A-Z][a-z]{3})?-[A-Z]{2})-\S")
# Characters XML 1.0 cannot carry at all; they would make the whole document invalid.
_XML_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def escape_ssml(text: str) -> str:
    """Text content for SSML: read verbatim, never interpreted as markup."""
    return escape(_XML_INVALID.sub("", text), {'"': "&quot;", "'": "&apos;"})


def rate_percent(rate: float) -> str:
    return f"{round((rate - 1) * 100):+d}%"


def voice_locale(voice: str) -> str:
    """The SSML language: the locale the voice name starts with."""
    match = _VOICE_LOCALE.match(voice)
    if match is None:
        raise ProviderError("bad_request", "azure_speech", UNKNOWN_AZURE_VOICE)
    return match.group(1)


def build_ssml(text: str, voice: str, rate: float) -> str:
    return (
        f'<speak version="1.0" xmlns="{SSML_NAMESPACE}" xml:lang="{voice_locale(voice)}">'
        f"<voice name={quoteattr(_XML_INVALID.sub('', voice))}>"
        f'<prosody rate="{rate_percent(rate)}">{escape_ssml(text)}</prosody>'
        "</voice></speak>"
    )


def endpoint(region: str, path: str = "cognitiveservices/v1") -> str:
    if not REGION_PATTERN.fullmatch(region or ""):
        raise ProviderError("bad_request", "azure_speech", BAD_REGION_MESSAGE)
    return f"https://{region}.tts.speech.microsoft.com/{path}"


def headers(key: str) -> dict[str, str]:
    return {"Ocp-Apim-Subscription-Key": key, "User-Agent": USER_AGENT}


def status_error_kind(status: int) -> ErrorKind:
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "server"
    return "bad_request"


def _retry_after(response: httpx2.Response) -> float:
    try:
        seconds = float(response.headers.get("Retry-After", "1"))
    except ValueError:
        seconds = 1.0
    return min(max(seconds, 0.0), MAX_RETRY_AFTER)


class AzureSynthesizer:
    def __init__(
        self,
        key: SecretStr,
        region: str,
        *,
        http_client: httpx2.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._key = key
        register_secret(key.get_secret_value())
        self.region = region
        self._client = http_client or httpx2.Client(timeout=TIMEOUT)
        self._sleep = sleep

    def engine_version(self, voice: str) -> str:
        return OUTPUT_FORMAT

    def _error(self, kind: ErrorKind, status: int | None = None) -> ProviderError:
        # Only the fixed message: Azure's response body is never passed through.
        retryable = kind in ("rate_limit", "server", "timeout", "network")
        return ProviderError(
            kind, "azure_speech", MESSAGES[kind], retryable=retryable, status_code=status
        )

    def _post(self, url: str, ssml: str) -> httpx2.Response:
        key = self._key.get_secret_value()
        request_headers = {
            **headers(key),
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
        }
        attempt = 0
        while True:
            try:
                response = self._client.post(
                    url, headers=request_headers, content=ssml.encode("utf-8"), timeout=TIMEOUT
                )
            except httpx2.TimeoutException:
                raise self._error("timeout") from None
            except httpx2.TransportError:
                raise self._error("network") from None
            if response.status_code < 400:
                return response
            kind = status_error_kind(response.status_code)
            if kind in ("rate_limit", "server") and attempt < MAX_RETRIES:
                attempt += 1
                self._sleep(_retry_after(response))
                continue
            raise self._error(kind, response.status_code)

    def synthesize(self, text: str, voice: VoiceSpec, *, operation_id: str) -> AudioResult:
        url = endpoint(self.region)
        response = self._post(url, build_ssml(text, voice.voice, voice.rate))
        audio = response.content
        duration_ms = wav_duration_ms(audio)
        if duration_ms is None:
            raise self._error("invalid_output", response.status_code)
        return AudioResult(
            audio=audio,
            mime="audio/wav",
            usage=Usage(audio_seconds=duration_ms / 1000, characters=len(text)),
            provenance=Provenance(
                provider="azure_speech",
                model=voice.voice,
                operation_id=operation_id,
                request_id=response.headers.get("X-RequestId"),
            ),
        )
