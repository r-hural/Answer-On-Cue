import logging
import re
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

ProviderName = Literal["openai", "anthropic", "azure_speech", "piper"]
ErrorKind = Literal[
    "auth",
    "rate_limit",
    "timeout",
    "network",
    "refused",
    "invalid_output",
    "unavailable",
    "bad_request",
    "server",
    "spending_limit",
]

REDACTED = "[hidden key]"
_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
_BEARER_PATTERN = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)
_known_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Remember a key value so redact() removes it even when it has no recognisable shape."""
    if value and len(value) >= 4:
        _known_secrets.add(value)
    install_log_redaction()


def redact(text: str) -> str:
    for secret in sorted(_known_secrets, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    text = _KEY_PATTERN.sub(REDACTED, text)
    return _BEARER_PATTERN.sub(lambda m: m.group(1) + REDACTED, text)


class RedactingFilter(logging.Filter):
    """Renders the record once and redacts it; SDK debug logs include response headers."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        return True


SDK_LOGGERS = ("openai", "anthropic", "httpx", "httpx2", "httpcore")
_redacting_filter = RedactingFilter()


def install_log_redaction() -> None:
    """Logger filters do not propagate, so attach to every SDK logger that exists by now."""
    names = set(SDK_LOGGERS) | {
        name for name in logging.root.manager.loggerDict if name.split(".")[0] in SDK_LOGGERS
    }
    for name in names:
        logger = logging.getLogger(name)
        if _redacting_filter not in logger.filters:
            logger.addFilter(_redacting_filter)


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    audio_seconds: float = 0.0
    characters: int = 0


class Provenance(BaseModel):
    provider: ProviderName
    model: str
    operation_id: str
    request_id: str | None = None
    served_by_fallback: bool = False


class Transcript(BaseModel):
    text: str
    language: str
    usage: Usage
    provenance: Provenance


class Structured(BaseModel, Generic[T]):  # noqa: UP046 - T is shared with the protocols
    value: T
    usage: Usage
    provenance: Provenance


class VoiceSpec(BaseModel):
    voice: str
    rate: float = 1.0


class AudioResult(BaseModel):
    audio: bytes
    mime: str
    usage: Usage
    provenance: Provenance


class ProviderError(Exception):
    def __init__(
        self,
        kind: ErrorKind,
        provider: ProviderName,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        self.kind = kind
        self.provider = provider
        self.retryable = retryable
        self.status_code = status_code
        self.message = redact(message)
        super().__init__(self.message)

    def __repr__(self) -> str:
        return f"ProviderError({self.kind!r}, {self.provider!r}, {self.message!r})"
