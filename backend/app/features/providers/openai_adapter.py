import httpx2
import openai
import pydantic
from pydantic import SecretStr

from app.features.languages.profiles import get_profile
from app.features.providers import limits
from app.features.providers.errors import MESSAGES, TRUNCATED_MESSAGE, map_sdk_error
from app.features.providers.types import (
    Message,
    Provenance,
    ProviderError,
    Structured,
    T,
    Transcript,
    Usage,
    register_secret,
)

GLOSSARY_LIMIT = 800
MIME_EXTENSIONS = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
}
TEXT_TIMEOUT = 120.0
TRANSCRIPTION_TIMEOUT = 300.0
MAX_RETRIES = limits.SDK_MAX_RETRIES


def glossary_prompt(glossary: list[str], prefix: str) -> str | None:
    """Whole terms only; the whole prompt, prefix included, stays within the limit."""
    prompt = ""
    for term in (t.strip() for t in glossary):
        if not term:
            continue
        candidate = f"{prompt}, {term}" if prompt else prefix + term
        if len(candidate) > GLOSSARY_LIMIT:
            break
        prompt = candidate
    return prompt or None


def _client(api_key: SecretStr, timeout: float, http_client: httpx2.Client | None) -> openai.OpenAI:
    register_secret(api_key.get_secret_value())
    return openai.OpenAI(
        api_key=api_key.get_secret_value(),
        max_retries=MAX_RETRIES,
        timeout=timeout,
        http_client=http_client,
    )


class OpenAITranscriber:
    def __init__(
        self, api_key: SecretStr, model: str, *, http_client: httpx2.Client | None = None
    ) -> None:
        self.model = model
        self.client = _client(api_key, TRANSCRIPTION_TIMEOUT, http_client)

    def transcribe(
        self,
        audio: bytes,
        mime: str,
        glossary: list[str],
        *,
        operation_id: str,
        language: str,
    ) -> Transcript:
        extension = MIME_EXTENSIONS.get(mime.split(";")[0].strip().lower())
        if extension is None:
            raise ProviderError("bad_request", "openai", f"Unsupported recording format: {mime}.")
        try:
            prefix = get_profile(language).glossary_prefix
        except ValueError:
            raise ProviderError(
                "bad_request", "openai", f"Unsupported language: {language}."
            ) from None
        prompt = glossary_prompt(glossary, prefix)
        try:
            response = self.client.audio.transcriptions.create(
                model=self.model,
                file=(f"answer{extension}", audio, mime),
                language=language,
                response_format="json",
                extra_headers={"idempotency-key": operation_id},
                **({"prompt": prompt} if prompt else {}),
            )
        except (openai.OpenAIError, pydantic.ValidationError) as exc:
            raise map_sdk_error(openai, "openai", exc) from None
        usage = getattr(response, "usage", None)
        seconds = getattr(response, "duration", None)
        if seconds is None and getattr(usage, "type", None) == "duration":
            seconds = usage.seconds
        return Transcript(
            text=response.text,
            language=language,
            usage=Usage(audio_seconds=float(seconds or 0.0)),
            provenance=Provenance(
                provider="openai",
                model=self.model,
                operation_id=operation_id,
                request_id=getattr(response, "_request_id", None),
            ),
        )


def _raw_status(raw) -> str | None:
    try:
        return raw.http_response.json().get("status")
    except ValueError:
        return None


class OpenAITextGenerator:
    def __init__(
        self, api_key: SecretStr, model: str, *, http_client: httpx2.Client | None = None
    ) -> None:
        self.model = model
        self.client = _client(api_key, TEXT_TIMEOUT, http_client)

    def generate_structured(
        self,
        task: str,
        system: str,
        messages: list[Message],
        schema: type[T],
        *,
        operation_id: str,
        max_output_tokens: int = 16000,
    ) -> Structured[T]:
        # The raw response lets a truncated answer be told apart from malformed JSON:
        # parse() raises on both before the response status can be read.
        try:
            raw = self.client.responses.with_raw_response.parse(
                model=self.model,
                instructions=system,
                input=[{"role": m.role, "content": m.content} for m in messages],
                text_format=schema,
                max_output_tokens=max_output_tokens,
                extra_headers={"idempotency-key": operation_id},
            )
        except openai.OpenAIError as exc:
            raise map_sdk_error(openai, "openai", exc) from None
        try:
            response = raw.parse()
        except pydantic.ValidationError as exc:
            if _raw_status(raw) == "incomplete":
                raise ProviderError("invalid_output", "openai", TRUNCATED_MESSAGE) from None
            raise map_sdk_error(openai, "openai", exc) from None

        refusals = [
            part.refusal
            for item in response.output
            if getattr(item, "type", None) == "message"
            for part in item.content
            if getattr(part, "type", None) == "refusal"
        ]
        if refusals:
            raise ProviderError("refused", "openai", f"{MESSAGES['refused']} ({refusals[0]})")
        if response.status == "incomplete":
            raise ProviderError("invalid_output", "openai", TRUNCATED_MESSAGE)
        value = response.output_parsed
        if value is None:
            raise ProviderError("invalid_output", "openai", MESSAGES["invalid_output"])

        usage = response.usage
        return Structured[schema](
            value=value,
            usage=Usage(
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
            ),
            provenance=Provenance(
                provider="openai",
                model=response.model or self.model,
                operation_id=operation_id,
                request_id=raw.request_id,
            ),
        )
