"""Maps SDK exceptions (openai and anthropic share the same names) to ProviderError."""

from types import ModuleType

import pydantic

from app.features.providers.types import ErrorKind, ProviderError, ProviderName, redact

SPENDING_LIMIT_SHORT = "The monthly spending ceiling is reached."
MESSAGES: dict[ErrorKind, str] = {
    "auth": "The provider rejected the key.",
    "rate_limit": "The provider's request limit was exceeded. Try again in a moment.",
    "timeout": "The provider did not respond in time.",
    "network": "Cannot connect to the provider.",
    "refused": "The model refused to answer.",
    "invalid_output": "The model's answer does not match the expected format.",
    "unavailable": "The provider is unavailable.",
    "bad_request": "The provider rejected the request.",
    "server": "The provider reported an error.",
    "spending_limit": SPENDING_LIMIT_SHORT,
}
TRUNCATED_MESSAGE = "The model's answer was cut off."
_DETAIL_LIMIT = 300


def _with_detail(kind: ErrorKind, detail: str | None) -> str:
    if not detail:
        return MESSAGES[kind]
    # Redact before truncating: a cut-off key would no longer match the stored secret.
    return f"{MESSAGES[kind]} ({redact(detail)[:_DETAIL_LIMIT]})"


def map_sdk_error(sdk: ModuleType, provider: ProviderName, exc: Exception) -> ProviderError:
    """Most specific first; the SDK text is kept as English detail and redacted by ProviderError."""
    status = getattr(exc, "status_code", None)
    detail = getattr(exc, "message", None) or str(exc)

    def error(kind: ErrorKind, retryable: bool) -> ProviderError:
        return ProviderError(
            kind, provider, _with_detail(kind, detail), retryable=retryable, status_code=status
        )

    if isinstance(exc, sdk.AuthenticationError | sdk.PermissionDeniedError):
        return error("auth", False)
    if isinstance(exc, sdk.RateLimitError):
        return error("rate_limit", True)
    if isinstance(exc, sdk.APITimeoutError):
        return error("timeout", True)
    if isinstance(exc, sdk.APIConnectionError):
        return error("network", True)
    if isinstance(exc, sdk.BadRequestError | sdk.NotFoundError | sdk.UnprocessableEntityError):
        return error("bad_request", False)
    if isinstance(exc, sdk.APIStatusError):
        if status is not None and status >= 500:
            return error("server", True)
        return error("bad_request", False)
    if isinstance(exc, pydantic.ValidationError):
        return ProviderError(
            "invalid_output",
            provider,
            _with_detail("invalid_output", f"validation errors: {exc.error_count()}"),
        )
    return error("server", False)
