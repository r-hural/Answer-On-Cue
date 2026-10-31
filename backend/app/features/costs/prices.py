"""The app's price table; every cost and estimate is priced from it."""

from dataclasses import dataclass

from app.features.providers.types import Usage

PRICES_CHECKED = "2026-10-20"


@dataclass(frozen=True)
class Price:
    input_per_m: float = 0  # USD per million input tokens
    output_per_m: float = 0  # USD per million output tokens
    audio_per_min: float = 0  # USD per audio minute
    chars_per_m: float = 0  # USD per million characters
    kind: str = ""  # overrides the kind inferred from the rates (a token-priced voice)


# (provider, model) → price. Text models are priced the same for every text role.
PRICES: dict[tuple[str, str], Price] = {
    ("openai", "gpt-transcribe"): Price(audio_per_min=0.0045),
    ("openai", "gpt-6.1-sol"): Price(input_per_m=2, output_per_m=10),
    # checked 2026-10-25; all output tokens at the audio rate, an upper bound
    ("openai", "gpt-realtime-2.1-mini"): Price(input_per_m=0.60, output_per_m=20.0, kind="voice"),
    ("anthropic", "claude-sonnet-5-5"): Price(input_per_m=2, output_per_m=10),
    # Neural voices without the free allowance: an upper bound, the bill may be lower.
    ("azure_speech", "neural"): Price(chars_per_m=16),
    ("piper", "local"): Price(),
}
FREE_PROVIDERS = ("piper", "browser")
TEXT_ROLES = ("generation", "assessment")


def _kind(role: str) -> str:
    return "text" if role in TEXT_ROLES else role


def _model_kind(price: Price) -> str:
    if price.kind:
        return price.kind
    if price.audio_per_min:
        return "transcription"
    if price.chars_per_m:
        return "voice"
    return "text"


def _highest(provider: str, role: str) -> Price | None:
    """The dearest known price of the provider for the same kind of call."""
    kind = _kind(role)
    candidates = [
        price
        for (name, _), price in PRICES.items()
        if name == provider and _model_kind(price) == kind
    ]
    if not candidates:
        return None
    return Price(
        input_per_m=max(p.input_per_m for p in candidates),
        output_per_m=max(p.output_per_m for p in candidates),
        audio_per_min=max(p.audio_per_min for p in candidates),
        chars_per_m=max(p.chars_per_m for p in candidates),
        kind=next((p.kind for p in candidates if p.kind), ""),
    )


def price_for(provider: str, model: str, role: str) -> tuple[Price, bool]:
    """(price, known); a model missing from the table gets its provider's highest price."""
    if provider in FREE_PROVIDERS:
        return Price(), True
    if provider == "azure_speech":
        return PRICES[("azure_speech", "neural")], True
    if (provider, model) in PRICES:
        return PRICES[(provider, model)], True
    highest = _highest(provider, role)
    if highest is None:  # a provider with no price at all: the dearest text price there is
        highest = _highest("openai", "generation") or Price()
    return highest, False


def usd(price: Price, usage: Usage) -> float:
    return (
        usage.input_tokens * price.input_per_m / 1_000_000
        + usage.output_tokens * price.output_per_m / 1_000_000
        + usage.audio_seconds / 60 * price.audio_per_min
        + usage.characters * price.chars_per_m / 1_000_000
    )


def cost_of(provider: str, model: str, role: str, usage: Usage) -> float:
    return usd(price_for(provider, model, role)[0], usage)
