"""The voice of a content language: matching, defaults, and the language's resolved voice.

A language never gets a voice of another language; when nothing fits, the browser voice.
No engine is loaded here, so the languages feature can import it cheaply.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.features.languages.profiles import PROFILES, LanguageCode, LanguageProfile, get_profile
from app.features.voice import azure_voices
from app.features.voice.catalog import BROWSER_VOICE, looks_like_openai_voice
from app.features.voice.piper_adapter import PiperSynthesizer

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.config import Settings
    from app.features.voice.service import VoiceProvider

logger = logging.getLogger(__name__)

VOICE_WRONG_LANGUAGE = (
    "This voice is for another language. Choose a voice for the content language."
)
_PROVIDERS = ("piper", "azure_speech", "browser", "openai")


def voice_matches_language(provider: str, voice: str, profile: LanguageProfile) -> bool:
    """Piper and Azure voices carry their language in the name; OpenAI and the browser voice
    read any language."""
    if provider == "piper":
        return voice.startswith(profile.piper_prefix + "-")
    if provider == "azure_speech":
        return voice.startswith(profile.azure_locale + "-")
    return provider in ("openai", "browser")


def language_of_voice(provider: str, voice: str) -> LanguageCode | None:
    """The language a Piper or Azure voice name belongs to; None for any other voice."""
    if provider not in ("piper", "azure_speech"):
        return None
    for profile in PROFILES.values():
        if voice_matches_language(provider, voice, profile):
            return profile.code
    return None


def default_voice(settings: Settings, provider: VoiceProvider, profile: LanguageProfile) -> str:
    """The voice a provider uses for a language when none is named."""
    from app.features.voice.service import resolve_voice

    if profile.code == "pl":
        # The global voice is Polish's (M6 Default 1): today's choice when it fits.
        chosen = resolve_voice(settings, provider, None)
        if voice_matches_language(provider, chosen, profile):
            return chosen
    if provider == "piper":
        return profile.default_piper_voice
    if provider == "azure_speech":
        if voice_matches_language(provider, settings.azure_voice_default, profile):
            return settings.azure_voice_default
        return azure_voices.voices_for(settings, profile)[0].short_name
    if provider == "openai":
        configured = settings.voice_name
        return configured if looks_like_openai_voice(configured) else settings.openai_voice_default
    if provider == "browser":
        return BROWSER_VOICE
    raise ValueError(f"no voice provider {provider!r}")


def _fallback(settings: Settings, profile: LanguageProfile) -> tuple[VoiceProvider, str]:
    if PiperSynthesizer(settings.effective_voices_dir).is_installed(profile.default_piper_voice):
        return "piper", profile.default_piper_voice
    return "browser", BROWSER_VOICE


def voice_for_language(
    session: Session, settings: Settings, code: str
) -> tuple[VoiceProvider, str]:
    """The language's row decides; Polish with no provider uses the global voice, another
    language its default Piper voice when installed, else the browser voice.

    Whether the voice is available (a key, an installed file) is not checked here."""
    from app.features.languages.service import get_language_row
    from app.features.voice.service import resolve_voice

    profile = get_profile(code)
    row = get_language_row(session, code)
    if row.voice_provider:
        provider = row.voice_provider
        voice = row.voice_name or (
            default_voice(settings, provider, profile) if provider in _PROVIDERS else ""
        )
    elif profile.code == "pl":
        provider = settings.voice_provider
        voice = resolve_voice(settings, provider, None)
    else:
        return _fallback(settings, profile)
    if provider not in _PROVIDERS or not voice_matches_language(provider, voice, profile):
        logger.warning(
            "The voice %s/%s is not a %s voice; using the fallback.",
            provider,
            voice,
            profile.name_en,
        )
        return _fallback(settings, profile)
    return provider, voice
