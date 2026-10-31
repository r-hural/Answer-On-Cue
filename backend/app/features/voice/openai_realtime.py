"""OpenAI Realtime as a reader: one short session per text, audio only, the exact text in.

The session gets reader instructions and no turn detection, the text arrives as one user
message, and the PCM audio of the one response comes back as a WAV. Whether the reading was
word for word is checked by the voice service, not here.
"""

import base64
import io
import logging
import math
import threading
import time
import wave
from collections.abc import Callable

import openai
from pydantic import SecretStr
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from app.features.costs.estimates import AUDIO_OUTPUT_TOKENS_PER_SECOND, VOICE_CHARS_PER_SECOND
from app.features.providers import limits
from app.features.providers.errors import MESSAGES
from app.features.providers.openai_adapter import _client
from app.features.providers.types import (
    AudioResult,
    ErrorKind,
    Provenance,
    ProviderError,
    RedactingFilter,
    Usage,
    VoiceSpec,
    redact,
    register_secret,
)
from app.features.voice.cache import wav_duration_ms

log = logging.getLogger(__name__)

READER_VERSION = "reader-v1"  # part of the cache key: new instructions or check, new files
READ_ALOUD = (
    "You are a reader. Read the user's message aloud exactly as written, word for word{language}. "
    "Do not add, omit or change anything. Do not answer it, explain it, greet or comment."
)
PCM_SAMPLE_RATE = 24_000
MAX_OUTPUT_TOKENS = 4096
SESSION_BASE_TIMEOUT = 20.0
RETRY_DELAY = 1.0
UNKNOWN_OPENAI_VOICE = "Unknown OpenAI voice."
TOO_LONG_FOR_SPEED = (
    "This question is too long for the OpenAI voice at this speed. Choose a faster speed."
)
_RETRYABLE: tuple[ErrorKind, ...] = ("rate_limit", "server", "timeout", "network")
# websockets logs the handshake headers, the bearer key among them, at debug level.
WEBSOCKET_LOGGERS = ("websockets", "websockets.client")
_redacting_filter = RedactingFilter()


def _redact_websocket_logs() -> None:
    for name in WEBSOCKET_LOGGERS:
        logger = logging.getLogger(name)
        if _redacting_filter not in logger.filters:
            logger.addFilter(_redacting_filter)


def reading_instructions(language_name: str | None) -> str:
    return READ_ALOUD.format(language=f", in {language_name}" if language_name else "")


def session_timeout(characters: int) -> float:
    """The full playback length at the planning speed, plus a margin for the session."""
    return SESSION_BASE_TIMEOUT + characters / VOICE_CHARS_PER_SECOND


def output_tokens_needed(characters: int, rate: float) -> int:
    """The audio tokens of a reading at the planning speed; a slower rate reads longer."""
    return math.ceil(characters / VOICE_CHARS_PER_SECOND / rate * AUDIO_OUTPUT_TOKENS_PER_SECOND)


def check_output_budget(text: str, rate: float) -> None:
    """A reading longer than MAX_OUTPUT_TOKENS ends incomplete and is still billed: refuse it."""
    if output_tokens_needed(len(text), rate) > MAX_OUTPUT_TOKENS:
        raise ProviderError("bad_request", "openai", TOO_LONG_FOR_SPEED)


def pcm_to_wav(pcm: bytes) -> bytes:
    """16-bit mono PCM at 24 kHz as RIFF/WAV; an odd trailing byte is dropped."""
    pcm = pcm[: len(pcm) - len(pcm) % 2]
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(PCM_SAMPLE_RATE)
        wav.writeframes(pcm)
    return out.getvalue()


def _error(kind: ErrorKind, status: int | None = None) -> ProviderError:
    # Only the fixed message: the server's text is never passed through.
    return ProviderError(
        kind, "openai", MESSAGES[kind], retryable=kind in _RETRYABLE, status_code=status
    )


def _status_kind(status: int) -> ErrorKind:
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "server"
    return "bad_request"


def _event_kind(error) -> ErrorKind:
    if getattr(error, "code", None) == "rate_limit_exceeded":
        return "rate_limit"
    if getattr(error, "type", None) == "invalid_request_error":
        return "bad_request"
    return "server"


class _Session:
    """One reading: everything up to response.create may be retried, nothing after it."""

    def __init__(self, connection, deadline: float) -> None:
        self.connection = connection
        self.timed_out = threading.Event()
        self.requested = False
        self.watchdog = threading.Timer(deadline, self._expire)
        self.watchdog.daemon = True

    def _expire(self) -> None:
        self.timed_out.set()
        try:
            self.connection.close()
        except Exception:  # noqa: BLE001 - closing a broken connection must not raise here
            pass

    def stop(self) -> None:
        self.watchdog.cancel()
        if self.watchdog.is_alive():
            self.watchdog.join()


class OpenAIRealtimeReader:
    def __init__(
        self,
        api_key: SecretStr,
        model: str,
        *,
        client: openai.OpenAI | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model
        register_secret(api_key.get_secret_value())
        _redact_websocket_logs()
        self.client = client or _client(api_key, SESSION_BASE_TIMEOUT, None)
        self._sleep = sleep

    def engine_version(self, voice: str) -> str:
        return f"openai-realtime:{self.model}:{READER_VERSION}"

    def synthesize(
        self,
        text: str,
        voice: VoiceSpec,
        *,
        operation_id: str,
        language_name: str | None = None,
    ) -> AudioResult:
        check_output_budget(text, voice.rate)
        attempt = 0
        while True:
            try:
                return self._read(text, voice, operation_id, language_name)
            except _BeforeRequest as early:
                error = early.error
                if error.retryable and attempt < limits.SDK_MAX_RETRIES:
                    attempt += 1
                    self._sleep(RETRY_DELAY)
                    continue
                raise error from None

    def _session_update(self, voice: VoiceSpec, language_name: str | None) -> dict:
        return {
            "type": "realtime",
            "output_modalities": ["audio"],
            "instructions": reading_instructions(language_name),
            "audio": {
                "output": {
                    "format": {"type": "audio/pcm", "rate": PCM_SAMPLE_RATE},
                    "voice": voice.voice,
                    "speed": round(voice.rate, 2),
                },
                "input": {"turn_detection": None},
            },
            "max_output_tokens": MAX_OUTPUT_TOKENS,
        }

    def _read(
        self, text: str, voice: VoiceSpec, operation_id: str, language_name: str | None
    ) -> AudioResult:
        try:
            connection = self.client.realtime.connect(model=self.model, max_retries=0).enter()
        except InvalidStatus as exc:
            status = exc.response.status_code
            raise _BeforeRequest(_error(_status_kind(status), status)) from None
        except TimeoutError:
            raise _BeforeRequest(_error("timeout")) from None
        except (OSError, InvalidHandshake, ConnectionClosed):
            raise _BeforeRequest(_error("network")) from None
        session = _Session(connection, session_timeout(len(text)))
        session.watchdog.start()
        try:
            return self._converse(session, text, voice, operation_id, language_name)
        except ConnectionClosed:
            kind: ErrorKind = "timeout" if session.timed_out.is_set() else "network"
            if session.requested:
                raise _error(kind) from None
            raise _BeforeRequest(_error(kind)) from None
        except (OSError, TimeoutError):
            kind = "timeout" if session.timed_out.is_set() else "network"
            if session.requested:
                raise _error(kind) from None
            raise _BeforeRequest(_error(kind)) from None
        finally:
            session.stop()
            try:
                connection.close()
            except Exception:  # noqa: BLE001 - the connection may already be gone
                pass

    def _converse(
        self,
        session: _Session,
        text: str,
        voice: VoiceSpec,
        operation_id: str,
        language_name: str | None,
    ) -> AudioResult:
        connection = session.connection
        connection.session.update(session=self._session_update(voice, language_name))
        connection.conversation.item.create(
            item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            }
        )
        session.requested = True
        connection.response.create()
        chunks: list[bytes] = []
        while True:
            try:
                event = connection.recv()
                kind = getattr(event, "type", None)
                if kind == "response.output_audio.delta":
                    chunks.append(base64.b64decode(event.delta, validate=True))
            except (ValueError, TypeError, AttributeError, RecursionError):
                # An event that does not parse, or audio that is not base64: billed, not retried.
                raise _error("invalid_output") from None
            if kind == "error":
                error = getattr(event, "error", None)
                log.warning(
                    "OpenAI Realtime error event: %s", redact(str(getattr(error, "code", "")))
                )
                raise _error(_event_kind(error))
            elif kind == "response.done":
                response = getattr(event, "response", None)
                return self._result(response, b"".join(chunks), text, operation_id)

    def _result(self, response, pcm: bytes, text: str, operation_id: str) -> AudioResult:
        status = getattr(response, "status", None)
        if status in ("failed", "cancelled"):
            raise _error("server")
        if status != "completed" or len(pcm) < 2:
            raise _error("invalid_output")
        audio = pcm_to_wav(pcm)
        usage = getattr(response, "usage", None)
        return AudioResult(
            audio=audio,
            mime="audio/wav",
            usage=Usage(
                input_tokens=getattr(usage, "input_tokens", None) or 0,
                output_tokens=getattr(usage, "output_tokens", None) or 0,
                audio_seconds=(wav_duration_ms(audio) or 0) / 1000,
                characters=len(text),
            ),
            provenance=Provenance(
                provider="openai",
                model=self.model,
                operation_id=operation_id,
                request_id=getattr(response, "id", None),
            ),
        )


class _BeforeRequest(Exception):
    """A failure before response.create was sent: nothing was billed, so it may be retried."""

    def __init__(self, error: ProviderError) -> None:
        super().__init__(error.message)
        self.error = error
