import anthropic
import httpx2
import pydantic
from pydantic import SecretStr

from app.features.providers import limits
from app.features.providers.errors import MESSAGES, TRUNCATED_MESSAGE, map_sdk_error
from app.features.providers.types import (
    Message,
    Provenance,
    ProviderError,
    Structured,
    T,
    Usage,
    register_secret,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
TEXT_TIMEOUT = 120.0
MAX_RETRIES = limits.SDK_MAX_RETRIES


class AnthropicTextGenerator:
    def __init__(
        self,
        api_key: SecretStr,
        model: str,
        *,
        fallbacks: bool = True,
        http_client: httpx2.Client | None = None,
    ) -> None:
        register_secret(api_key.get_secret_value())
        self.model = model
        self.fallbacks = fallbacks
        self.client = anthropic.Anthropic(
            api_key=api_key.get_secret_value(),
            max_retries=MAX_RETRIES,
            timeout=TEXT_TIMEOUT,
            http_client=http_client,
        )

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
        # Only the beta namespace exposes parse() with a raw response, which is needed to read
        # stop_reason before the output is parsed. Without betas it sends the same request
        # as messages.parse(). No sampling parameters and no assistant prefill, by design.
        extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"} if self.fallbacks else {}
        try:
            raw = self.client.beta.messages.with_raw_response.parse(
                model=self.model,
                max_tokens=max_output_tokens,
                system=system,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                output_format=schema,
                extra_headers={"idempotency-key": operation_id},
                **extra,
            )
        except anthropic.AnthropicError as exc:
            raise map_sdk_error(anthropic, "anthropic", exc) from None

        _check_stop_reason(raw)
        try:
            response = raw.parse()
        except pydantic.ValidationError as exc:
            raise map_sdk_error(anthropic, "anthropic", exc) from None
        value = response.parsed_output
        if value is None:
            raise ProviderError("invalid_output", "anthropic", MESSAGES["invalid_output"])

        iterations = getattr(response.usage, "iterations", None) or []
        return Structured[schema](
            value=value,
            usage=Usage(
                input_tokens=response.usage.input_tokens or 0,
                output_tokens=response.usage.output_tokens or 0,
            ),
            provenance=Provenance(
                provider="anthropic",
                model=response.model or self.model,
                operation_id=operation_id,
                request_id=raw.request_id,
                served_by_fallback=any(i.type == "fallback_message" for i in iterations),
            ),
        )


def _check_stop_reason(raw) -> None:
    try:
        body = raw.http_response.json()
    except ValueError:
        raise ProviderError("invalid_output", "anthropic", MESSAGES["invalid_output"]) from None
    stop_reason = body.get("stop_reason")
    if stop_reason == "refusal":
        category = (body.get("stop_details") or {}).get("category")
        message = MESSAGES["refused"] + (f" (category: {category})" if category else "")
        raise ProviderError("refused", "anthropic", message)
    if stop_reason == "max_tokens":
        raise ProviderError("invalid_output", "anthropic", TRUNCATED_MESSAGE)
