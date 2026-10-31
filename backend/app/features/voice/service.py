"""Chooses the voice engine from settings, synthesizes through the file cache."""

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel

from app.config import Settings
from app.db.session import SessionLocal
from app.features.costs import estimates
from app.features.costs.spending import guard_for
from app.features.languages.profiles import LanguageProfile, get_profile
from app.features.providers.base import Transcriber
from app.features.providers.registry import (
    RecordingSynthesizer,
    RecordingTranscriber,
    missing_key_message,
)
from app.features.providers.types import AudioResult, ProviderError, Transcript, VoiceSpec
from app.features.providers.usage import get_durable_usage_recorder
from app.features.voice import azure_voices, piper_catalog, verbatim
from app.features.voice.cache import VoiceCache, cache_key, normalize_text, wav_duration_ms
from app.features.voice.catalog import (
    BROWSER_LABEL,
    BROWSER_VOICE,
    DEFAULT_PIPER_VOICE,
    OPENAI_VOICES,
    PIPER_VOICES,
    Gender,
    looks_like_azure_voice,
    looks_like_openai_voice,
)
from app.features.voice.openai_realtime import UNKNOWN_OPENAI_VOICE, check_output_budget
from app.features.voice.per_language import (
    VOICE_WRONG_LANGUAGE,
    default_voice,
    language_of_voice,
    voice_for_language,
    voice_matches_language,
)
from app.features.voice.piper_adapter import PiperSynthesizer

VoiceProvider = Literal["piper", "azure_speech", "browser", "openai"]

SAMPLE_TEXT = "Omów przyczyny i skutki rewolucji przemysłowej w Europie w XVIII i XIX wieku."
MAX_TEXT_LENGTH = 2000
INVALID_TEXT_MESSAGE = "The question text is empty or too long."
MISSING_REGION_MESSAGE = "No Azure Speech region. Add it in Settings."
# A reading that fails the word-for-word check gets one more fresh session.
VERBATIM_RETRIES = 1
VOICE_NOT_VERBATIM = "The OpenAI voice did not read this question exactly, so it is shown as text."
NEED_LANGUAGE = "Name the content language or a voice provider."
OPENAI_NEEDS_LANGUAGE = "The OpenAI voice needs the content language."
UNKNOWN_LANGUAGE = "Unknown language."


class InvalidText(ValueError):
    def __init__(self) -> None:
        super().__init__(INVALID_TEXT_MESSAGE)


class InvalidRequest(ValueError):
    """A request the voice service refuses before any call (422 with its message)."""


class WrongLanguage(InvalidRequest):
    def __init__(self) -> None:
        super().__init__(VOICE_WRONG_LANGUAGE)


class NotVerbatim(ProviderError):
    """Every reading differed from the text: nothing is cached, the question is shown as text."""

    def __init__(self) -> None:
        super().__init__("invalid_output", "openai", VOICE_NOT_VERBATIM)


class VoiceEngine(Protocol):
    """A Synthesizer that also names its engine version, which goes into the cache key."""

    def synthesize(self, text: str, voice: VoiceSpec, *, operation_id: str) -> AudioResult: ...

    def engine_version(self, voice: str) -> str: ...


@dataclass(frozen=True)
class SpeakResult:
    provider: VoiceProvider
    voice: str
    rate: float
    cache_key: str | None  # None for browser
    cached: bool
    duration_ms: int | None  # None for browser


class VoiceInfo(BaseModel):
    provider: VoiceProvider
    id: str
    label: str
    installed: bool
    gender: Gender | None
    language: str | None = None  # the content language of a Piper or Azure voice


_engines_lock = threading.Lock()
_piper_engines: dict[Path, PiperSynthesizer] = {}
# The last Azure engine, reused while the key and region stay the same (one HTTP client).
_azure_engine: tuple[tuple[str, str], VoiceEngine] | None = None
# The last OpenAI Realtime reader, reused while the key and model stay the same.
_openai_engine: tuple[tuple[str, str], VoiceEngine] | None = None
# One lock per key bucket: two requests for the same uncached text synthesize it once.
_key_locks = [threading.Lock() for _ in range(64)]


def _piper(settings: Settings) -> PiperSynthesizer:
    """One instance per voices folder, so loaded models stay in memory between requests."""
    voices_dir = settings.effective_voices_dir.resolve()
    with _engines_lock:
        if voices_dir not in _piper_engines:
            _piper_engines[voices_dir] = PiperSynthesizer(voices_dir)
        return _piper_engines[voices_dir]


def _azure_ready(settings: Settings) -> bool:
    key = settings.azure_speech_key
    return bool(key and key.get_secret_value().strip() and (settings.azure_speech_region or ""))


def _openai_ready(settings: Settings) -> bool:
    key = settings.openai_api_key
    return bool(key and key.get_secret_value().strip())


def get_synthesizer(settings: Settings, provider: VoiceProvider) -> VoiceEngine:
    """Tests replace this to run the service without a real engine."""
    if provider == "piper":
        return _piper(settings)
    if provider == "azure_speech":
        from app.features.voice.azure_adapter import AzureSynthesizer

        key = settings.azure_speech_key
        if key is None or not key.get_secret_value().strip():
            raise ProviderError("unavailable", "azure_speech", missing_key_message("azure_speech"))
        region = (settings.azure_speech_region or "").strip()
        if not region:
            raise ProviderError("unavailable", "azure_speech", MISSING_REGION_MESSAGE)
        global _azure_engine
        identity = (key.get_secret_value(), region)
        with _engines_lock:
            if _azure_engine is None or _azure_engine[0] != identity:
                _azure_engine = (identity, AzureSynthesizer(key, region))
            return _azure_engine[1]
    if provider == "openai":
        from app.features.voice.openai_realtime import OpenAIRealtimeReader

        if not _openai_ready(settings):
            raise ProviderError("unavailable", "openai", missing_key_message("openai"))
        global _openai_engine
        key = settings.openai_api_key
        identity = (key.get_secret_value(), settings.openai_voice_model)
        with _engines_lock:
            if _openai_engine is None or _openai_engine[0] != identity:
                _openai_engine = (identity, OpenAIRealtimeReader(key, settings.openai_voice_model))
            return _openai_engine[1]
    raise ValueError(f"no synthesizer for {provider}")


class OpenAIRecordingSynthesizer(RecordingSynthesizer):
    """An OpenAI Realtime reader whose readings are recorded and guarded, priced by tokens."""

    def __init__(self, engine, model: str, recorder, *, guard=None) -> None:
        super().__init__(engine, model, recorder, guard=guard)
        self.provider = "openai"

    def synthesize(
        self,
        text: str,
        voice: VoiceSpec,
        *,
        operation_id: str,
        language_name: str | None = None,
    ) -> AudioResult:
        return self._call(
            operation_id,
            "voice.synthesize",
            estimates.voice_usd("openai", self.model, len(text)),
            # A plain VoiceEngine (a test fake) takes no language: only a given one is passed.
            lambda: self.inner.synthesize(
                text,
                voice,
                operation_id=operation_id,
                **({"language_name": language_name} if language_name else {}),
            ),
        )


class VoiceVerifyTranscriber(RecordingTranscriber):
    """The transcription that checks a reading, recorded as its own task: voice.verify."""

    def transcribe(
        self,
        audio: bytes,
        mime: str,
        glossary: list[str],
        *,
        operation_id: str,
        language: str,
    ) -> Transcript:
        seconds = estimates.audio_seconds(audio, mime)
        return self._call(
            operation_id,
            "voice.verify",
            estimates.transcription_usd(self.provider, self.model, seconds),
            lambda: self.inner.transcribe(
                audio, mime, glossary, operation_id=operation_id, language=language
            ),
        )


def verify_transcriber(settings: Settings) -> Transcriber:
    """Tests replace this. The existing transcription model, with the OpenAI key."""
    from app.features.providers.openai_adapter import OpenAITranscriber

    if not _openai_ready(settings):
        raise ProviderError("unavailable", "openai", missing_key_message("openai"))
    key, model = settings.openai_api_key, settings.transcription_model
    return VoiceVerifyTranscriber(
        lambda: OpenAITranscriber(key, model),
        "openai",
        model,
        get_durable_usage_recorder(settings),
        guard=guard_for(settings),
    )


def recorded(settings: Settings, provider: VoiceProvider, voice: str, engine: VoiceEngine):
    """Azure and OpenAI are paid: their calls are recorded. Piper is free and local: not."""
    if provider == "openai":
        return OpenAIRecordingSynthesizer(
            engine,
            settings.openai_voice_model,
            get_durable_usage_recorder(settings),
            guard=guard_for(settings),
        )
    if provider != "azure_speech":
        return engine
    return RecordingSynthesizer(
        engine, voice, get_durable_usage_recorder(settings), guard=guard_for(settings)
    )


def _voice_cache(settings: Settings) -> VoiceCache:
    return VoiceCache(settings.data_dir / "voice-cache")


def resolve_voice(settings: Settings, provider: VoiceProvider, voice: str | None) -> str:
    if voice:
        return voice
    if provider == "browser":
        return BROWSER_VOICE
    configured = settings.voice_name
    if provider == "openai":
        return configured if looks_like_openai_voice(configured) else settings.openai_voice_default
    if provider == "azure_speech" and not looks_like_azure_voice(configured):
        return settings.azure_voice_default
    if provider == "piper" and (
        looks_like_azure_voice(configured) or looks_like_openai_voice(configured)
    ):
        return DEFAULT_PIPER_VOICE
    return configured


def clean_text(text: str) -> str:
    text = normalize_text(text)
    if not text or len(text) > MAX_TEXT_LENGTH:
        raise InvalidText()
    return text


def _profile(language: str) -> LanguageProfile:
    try:
        return get_profile(language)
    except ValueError:
        raise InvalidRequest(UNKNOWN_LANGUAGE) from None


def speak(
    settings: Settings,
    text: str,
    *,
    language: str | None = None,
    provider: str | None = None,
    voice: str | None = None,
    rate: float | None = None,
) -> SpeakResult:
    """With a language, its voice reads the text, and a voice of another language is refused.
    Without one (old rounds, the global voice's preview) a provider must be named."""
    text = clean_text(text)
    profile = _profile(language) if language is not None else None
    if provider is None:
        if profile is None:
            raise InvalidRequest(NEED_LANGUAGE)
        # Closed before any synthesis: a slow reading holds no database connection.
        with SessionLocal(settings.effective_database_url) as session:
            chosen, resolved = voice_for_language(session, settings, profile.code)
        voice_id = voice or resolved
    else:
        chosen = provider
        voice_id = voice or (
            default_voice(settings, chosen, profile)
            if profile is not None
            else resolve_voice(settings, chosen, None)
        )
    if profile is not None and not voice_matches_language(chosen, voice_id, profile):
        raise WrongLanguage()
    if chosen == "openai" and profile is None:
        raise InvalidRequest(OPENAI_NEEDS_LANGUAGE)
    speed = settings.voice_rate if rate is None else rate
    if chosen == "browser":
        return SpeakResult(chosen, voice_id, speed, None, False, None)
    if chosen == "openai" and voice_id not in OPENAI_VOICES:
        raise ProviderError("bad_request", "openai", UNKNOWN_OPENAI_VOICE)
    if chosen == "openai":
        check_output_budget(text, speed)  # before any call, so no usage row

    engine = get_synthesizer(settings, chosen)
    version = engine.engine_version(voice_id)
    key = cache_key(chosen, voice_id, speed, version, text)
    cache = _voice_cache(settings)
    with _key_locks[int(key[:8], 16) % len(_key_locks)]:
        if cache.get(key) is not None:
            meta = cache.read_meta(key) or {}
            duration = meta.get("duration_ms")
            if duration is None:
                duration = wav_duration_ms(cache.path(key).read_bytes())
            return SpeakResult(chosen, voice_id, speed, key, True, duration)
        # A cached replay returns above: no call, no usage row.
        spec = VoiceSpec(voice=voice_id, rate=speed)
        verified = None
        if chosen == "openai":
            result, verified = _read_checked(
                settings,
                engine,
                text,
                spec,
                key,
                language_code=profile.code,
                language_name=profile.name_en,
            )
        else:
            result = recorded(settings, chosen, voice_id, engine).synthesize(
                text, spec, operation_id=f"voice:{key[:16]}"
            )
        duration = wav_duration_ms(result.audio)
        meta = {
            "provider": chosen,
            "voice": voice_id,
            "rate": speed,
            "text": text,
            "engine_version": version,
            "duration_ms": duration,
        }
        if verified is not None:
            meta["verified"] = verified
        cache.put(key, result.audio, meta)
    return SpeakResult(chosen, voice_id, speed, key, False, duration)


def _read_checked(
    settings: Settings,
    engine: VoiceEngine,
    text: str,
    spec: VoiceSpec,
    key: str,
    *,
    language_code: str,
    language_name: str,
) -> tuple[AudioResult, dict]:
    """Read in a fresh session, transcribe the audio and compare it with the text; a mismatch
    gets one more session. Only a reading that passed comes back (and so is cached).

    The glossary stays empty: the question as a hint would bias the transcript towards it."""
    reader = recorded(settings, "openai", spec.voice, engine)
    checker = verify_transcriber(settings)
    base = f"voice:{key[:16]}"
    for attempt in range(1 + VERBATIM_RETRIES):
        operation = base if attempt == 0 else f"{base}:r{attempt}"
        result = reader.synthesize(text, spec, operation_id=operation, language_name=language_name)
        heard = checker.transcribe(
            result.audio,
            result.mime,
            [],
            operation_id=f"{operation}:verify",
            language=language_code,
        )
        match = verbatim.compare(text, heard.text)
        if match.ok:
            return result, {
                "model": settings.transcription_model,
                "attempts": attempt + 1,
                "matched": round(match.matched, 3),
                "heard": heard.text,
            }
    raise NotVerbatim()


def preview(
    settings: Settings,
    provider: str,
    voice: str | None,
    rate: float | None = None,
    *,
    language: str | None = None,
) -> SpeakResult:
    """The language's preview sentence; without a language, the voice's own, else Polish."""
    code = language or language_of_voice(provider, voice or "") or "pl"
    profile = _profile(code)
    return speak(
        settings, profile.preview_sentence, language=code, provider=provider, voice=voice, rate=rate
    )


def cache_file(settings: Settings, cache_key: str) -> Path:
    path = _voice_cache(settings).get(cache_key)
    if path is None:
        raise FileNotFoundError(cache_key)
    return path


def list_voices(
    settings: Settings, language: str | None = None, *, refresh: bool = False
) -> list[VoiceInfo]:
    """The language's Piper and Azure voices, then the OpenAI and browser voices.

    Without a language, Polish's (the global voice is Polish's), with the Polish Azure voices
    listed as not installed when there is no key: Settings → Voice shows them so."""
    profile = _profile(language or "pl")
    voices_dir = settings.effective_voices_dir
    if refresh:
        piper_catalog.refresh(voices_dir)
        azure_voices.refresh(settings)
    piper = PiperSynthesizer(voices_dir)
    offered = piper_catalog.offered(piper_catalog.load_catalog(voices_dir), profile)
    voices = [
        VoiceInfo(
            provider="piper",
            id=entry.key,
            label=piper_catalog.label(entry),
            installed=piper.is_installed(entry.key),
            gender=PIPER_VOICES[entry.key][1] if entry.key in PIPER_VOICES else None,
            language=profile.code,
        )
        for entry in offered
    ]
    # Other Piper voices of the language the user put in the folder by hand.
    listed = {entry.key for entry in offered}
    if voices_dir.is_dir():
        for model in sorted(voices_dir.glob(f"{profile.piper_prefix}-*.onnx")):
            voice_id = model.name.removesuffix(".onnx")
            if voice_id not in listed and piper.is_installed(voice_id):
                voices.append(
                    VoiceInfo(
                        provider="piper",
                        id=voice_id,
                        label=voice_id,
                        installed=True,
                        gender=None,
                        language=profile.code,
                    )
                )
    azure_ready = _azure_ready(settings)
    if azure_ready:
        azure = azure_voices.voices_for(settings, profile)
    elif language is None:
        azure = list(azure_voices.PINNED[profile.azure_locale])
    else:
        azure = []  # no key: no Azure voice is offered for a language
    voices += [
        VoiceInfo(
            provider="azure_speech",
            id=voice.short_name,
            label=voice.label,
            installed=azure_ready,
            gender=voice.gender,
            language=profile.code,
        )
        for voice in azure
    ]
    openai_ready = _openai_ready(settings)
    voices += [
        VoiceInfo(
            provider="openai", id=voice_id, label=label, installed=openai_ready, gender=gender
        )
        for voice_id, (label, gender) in OPENAI_VOICES.items()
    ]
    voices.append(
        VoiceInfo(
            provider="browser", id=BROWSER_VOICE, label=BROWSER_LABEL, installed=True, gender=None
        )
    )
    return voices
