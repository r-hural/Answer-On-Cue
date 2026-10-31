"""Content languages: which are enabled, their voices, and the language of a subject."""

import typing

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.features.languages.models import ContentLanguage
from app.features.languages.profiles import (
    PROFILES,
    SUPPORTED,
    LanguageCode,
    LanguageProfile,
    get_profile,
)
from app.features.languages.schemas import LanguageOut, LanguagePatch
from app.features.library.models import Subject

LANGUAGE_UNKNOWN = "Unknown language."
LANGUAGE_NOT_ENABLED = "This content language is not enabled. Choose one of the enabled languages."
LANGUAGE_LOCKED = "The language of a subject can't be changed after it is created."
LANGUAGE_IN_USE = "Subjects use this language, so it can't be disabled."
LAST_LANGUAGE = "At least one language must stay enabled."
VOICE_PROVIDER_UNKNOWN = "Unknown voice provider."
VOICE_NAME_WITHOUT_PROVIDER = "Choose a voice provider first."
VOICE_UNKNOWN = "Unknown voice."
SUBJECT_NOT_FOUND = "Subject not found."


def subject_language(session: Session, subject_id: int) -> LanguageCode:
    language = session.scalar(select(Subject.language).where(Subject.id == subject_id))
    if language is None:
        raise HTTPException(404, SUBJECT_NOT_FOUND)
    return language


def profile_for_subject(session: Session, subject_id: int) -> LanguageProfile:
    return get_profile(subject_language(session, subject_id))


def enabled_codes(session: Session) -> list[LanguageCode]:
    enabled = set(
        session.scalars(select(ContentLanguage.code).where(ContentLanguage.enabled.is_(True)))
    )
    return [code for code in SUPPORTED if code in enabled]


def get_language_row(session: Session, code: str) -> ContentLanguage:
    row = session.get(ContentLanguage, code) if code in PROFILES else None
    if row is None:
        raise HTTPException(404, LANGUAGE_UNKNOWN)
    return row


def require_enabled(session: Session, code: str) -> None:
    enabled = session.scalar(select(ContentLanguage.enabled).where(ContentLanguage.code == code))
    if code not in PROFILES or not enabled:
        raise HTTPException(422, LANGUAGE_NOT_ENABLED)


def _subject_counts(session: Session) -> dict[str, int]:
    rows = session.execute(select(Subject.language, func.count()).group_by(Subject.language))
    return dict(rows.all())


def _language_out(row: ContentLanguage, subject_count: int) -> LanguageOut:
    profile = PROFILES[row.code]
    return LanguageOut(
        code=row.code,
        bcp47=profile.bcp47,
        name_en=profile.name_en,
        name_native=profile.name_native,
        quotes=profile.quotes,
        enabled=row.enabled,
        voice_provider=row.voice_provider,
        voice_name=row.voice_name,
        subject_count=subject_count,
    )


def list_languages(session: Session) -> list[LanguageOut]:
    """Two statements whatever the data: the rows, then the subjects per language."""
    rows = {row.code: row for row in session.scalars(select(ContentLanguage))}
    counts = _subject_counts(session)
    return [_language_out(rows[code], counts.get(code, 0)) for code in SUPPORTED if code in rows]


def _check_voice(profile: LanguageProfile, provider: str, name: str) -> None:
    """A voice must read the language; a Piper voice need not be installed yet."""
    # Imported here so this feature does not load the voice engines at import time.
    from app.features.voice.catalog import BROWSER_VOICE, OPENAI_VOICES
    from app.features.voice.openai_realtime import UNKNOWN_OPENAI_VOICE
    from app.features.voice.per_language import VOICE_WRONG_LANGUAGE, voice_matches_language

    if not voice_matches_language(provider, name, profile):
        raise HTTPException(422, VOICE_WRONG_LANGUAGE)
    if provider == "openai" and name not in OPENAI_VOICES:
        raise HTTPException(422, UNKNOWN_OPENAI_VOICE)
    if provider == "browser" and name != BROWSER_VOICE:
        raise HTTPException(422, VOICE_UNKNOWN)


def update_language(session: Session, code: str, patch: LanguagePatch) -> LanguageOut:
    # Imported here so this feature does not load the voice engines.
    from app.features.voice.service import VoiceProvider

    row = get_language_row(session, code)
    fields = patch.model_fields_set
    counts = _subject_counts(session)
    if "enabled" in fields and patch.enabled is False and row.enabled:
        if counts.get(code, 0):
            raise HTTPException(409, LANGUAGE_IN_USE)
        if enabled_codes(session) == [code]:
            raise HTTPException(409, LAST_LANGUAGE)
    if "voice_provider" in fields and patch.voice_provider is not None:
        if patch.voice_provider not in typing.get_args(VoiceProvider):
            raise HTTPException(422, VOICE_PROVIDER_UNKNOWN)
    provider = patch.voice_provider if "voice_provider" in fields else row.voice_provider
    name = patch.voice_name if "voice_name" in fields else row.voice_name
    if "voice_provider" in fields and patch.voice_provider is None:
        name = None
    if name is not None and provider is None:
        raise HTTPException(422, VOICE_NAME_WITHOUT_PROVIDER)
    touched = "voice_provider" in fields or "voice_name" in fields
    if touched and provider is not None and name is not None:
        _check_voice(PROFILES[code], provider, name)
    if "enabled" in fields and patch.enabled is not None:
        row.enabled = patch.enabled
    row.voice_provider = provider
    row.voice_name = name
    session.commit()
    return _language_out(row, counts.get(code, 0))
