"""The Azure voices per locale: Azure's voices list for the region, cached, with a pinned fallback.

Only refresh() goes to the network, and only with a key and a valid region. The cache holds
the voices' names, locales, labels and genders: never the key.
"""

import contextlib
import json
import logging
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2

from app.config import Settings
from app.features.languages.profiles import PROFILES, LanguageProfile
from app.features.providers.types import register_secret
from app.features.voice.azure_adapter import REGION_PATTERN, endpoint, headers
from app.features.voice.catalog import AZURE_VOICES, Gender

logger = logging.getLogger(__name__)

VOICES_LIST_PATH = "cognitiveservices/voices/list"
CACHE_MAX_AGE = timedelta(days=7)
MAX_LIST_BYTES = 5_000_000  # the cache file (four fields per voice)
MAX_RESPONSE_BYTES = 50_000_000  # Azure's own list, with every locale and style
TIMEOUT = httpx2.Timeout(30.0, connect=15.0)
# The xx-YY (or xx-Script-YY) locale that starts an Azure voice name.
_SHORT_NAME = re.compile(r"^[a-z]{2,3}(?:-[A-Z][a-z]{3})?-[A-Z]{2}-[A-Za-z0-9]+$")
_GENDERS: dict[str, Gender] = {"Female": "female", "Male": "male"}

# Tests replace this to send a refresh through a MockTransport.
http_client_factory: Callable[[], httpx2.Client | None] = lambda: None  # noqa: E731


@dataclass(frozen=True)
class AzureVoice:
    short_name: str
    locale: str
    label: str
    gender: Gender | None


def _voice(short_name: str, gender: Gender) -> AzureVoice:
    locale = short_name.rsplit("-", 1)[0]
    name = short_name.rsplit("-", 1)[1].removesuffix("Neural")
    return AzureVoice(short_name, locale, f"{name} (Azure, neural)", gender)


# Polish keeps catalog.AZURE_VOICES; the others are the owner's picks (owner check at M6).
PINNED: dict[str, tuple[AzureVoice, ...]] = {
    "pl-PL": tuple(
        AzureVoice(short_name, "pl-PL", label, gender)
        for short_name, (label, gender) in AZURE_VOICES.items()
    ),
    "en-GB": (
        _voice("en-GB-SoniaNeural", "female"),
        _voice("en-GB-RyanNeural", "male"),
        _voice("en-GB-LibbyNeural", "female"),
    ),
    "de-DE": (
        _voice("de-DE-KatjaNeural", "female"),
        _voice("de-DE-ConradNeural", "male"),
        _voice("de-DE-AmalaNeural", "female"),
    ),
    "fr-FR": (
        _voice("fr-FR-DeniseNeural", "female"),
        _voice("fr-FR-HenriNeural", "male"),
        _voice("fr-FR-EloiseNeural", "female"),
    ),
    "es-ES": (
        _voice("es-ES-ElviraNeural", "female"),
        _voice("es-ES-AlvaroNeural", "male"),
        _voice("es-ES-AbrilNeural", "female"),
    ),
    "it-IT": (
        _voice("it-IT-ElsaNeural", "female"),
        _voice("it-IT-DiegoNeural", "male"),
        _voice("it-IT-IsabellaNeural", "female"),
    ),
    "uk-UA": (
        _voice("uk-UA-PolinaNeural", "female"),
        _voice("uk-UA-OstapNeural", "male"),
    ),
}
_LOCALES = frozenset(profile.azure_locale for profile in PROFILES.values())


def _region(settings: Settings) -> str | None:
    region = (settings.azure_speech_region or "").strip()
    return region if REGION_PATTERN.fullmatch(region) else None


def cache_path(settings: Settings) -> Path | None:
    """The region's cache file; None (no cache, no fetch) without a valid region."""
    region = _region(settings)
    if region is None:
        return None
    return settings.effective_voices_dir / f"azure-voices-{region}.json"


def _ordered(voices: list[AzureVoice]) -> dict[str, tuple[AzureVoice, ...]]:
    """Per locale: the pinned voices first, in their order, then the rest by name."""
    by_locale: dict[str, list[AzureVoice]] = {}
    for voice in voices:
        by_locale.setdefault(voice.locale, []).append(voice)
    result = {}
    for locale, found in by_locale.items():
        pinned = [voice.short_name for voice in PINNED.get(locale, ())]
        rank = {name: index for index, name in enumerate(pinned)}
        found.sort(key=lambda voice: (rank.get(voice.short_name, len(rank)), voice.short_name))
        result[locale] = tuple(found)
    return result


def _load(path: Path) -> dict[str, tuple[AzureVoice, ...]] | None:
    try:
        if path.stat().st_size > MAX_LIST_BYTES:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        voices = [
            AzureVoice(
                short_name=item["short_name"],
                locale=item["locale"],
                label=item["label"],
                gender=item["gender"] if item["gender"] in ("female", "male") else None,
            )
            for item in raw["voices"]
        ]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not voices or not all(
        _SHORT_NAME.fullmatch(v.short_name) and v.short_name.startswith(v.locale + "-")
        for v in voices
    ):
        return None
    return _ordered(voices)


def voices_for(settings: Settings, profile: LanguageProfile) -> list[AzureVoice]:
    """The region's cached voices of the language's locale, else the pinned ones."""
    path = cache_path(settings)
    cached = _load(path) if path is not None else None
    if cached and cached.get(profile.azure_locale):
        return list(cached[profile.azure_locale])
    return list(PINNED[profile.azure_locale])


def _label(item: dict, short_name: str) -> str:
    known = AZURE_VOICES.get(short_name)
    if known is not None:
        return known[0]
    name = item.get("DisplayName") or item.get("LocalName")
    if not isinstance(name, str) or not name.strip():
        name = short_name.rsplit("-", 1)[1].removesuffix("Neural")
    return f"{name.strip()} (Azure, neural)"


def _parse(body: bytes) -> list[AzureVoice] | None:
    try:
        raw = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(raw, list):
        return None
    voices = []
    for item in raw:
        if not isinstance(item, dict) or item.get("VoiceType") != "Neural":
            continue
        if item.get("Status") == "Deprecated":
            continue
        short_name, locale = item.get("ShortName"), item.get("Locale")
        if not isinstance(short_name, str) or not isinstance(locale, str):
            continue
        if locale not in _LOCALES or not _SHORT_NAME.fullmatch(short_name):
            continue
        if not short_name.startswith(locale + "-"):
            continue
        gender = item.get("Gender")
        gender = _GENDERS.get(gender) if isinstance(gender, str) else None
        voices.append(AzureVoice(short_name, locale, _label(item, short_name), gender))
    return voices or None


def _fresh(path: Path, now: datetime) -> bool:
    try:
        changed = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError:
        return False
    return now - changed < CACHE_MAX_AGE


def refresh(
    settings: Settings, *, now: datetime | None = None, http_client: httpx2.Client | None = None
) -> bool:
    """Fetches the region's voices list when the cache is missing or older than CACHE_MAX_AGE.

    Only with a key and a valid region. True when a new file was written; a failure keeps the
    old file and is only logged, by status code or error type (never the key)."""
    key = settings.azure_speech_key
    secret = key.get_secret_value().strip() if key is not None else ""
    path = cache_path(settings)
    if not secret or path is None or _fresh(path, now or datetime.now(UTC)):
        return False
    register_secret(secret)
    http_client = http_client or http_client_factory()
    client = http_client or httpx2.Client(timeout=TIMEOUT)
    tmp: str | None = None
    try:
        response = client.get(
            endpoint(_region(settings), VOICES_LIST_PATH), headers=headers(secret)
        )
        if response.status_code >= 400:
            logger.warning("Could not refresh the Azure voices list: code %s", response.status_code)
            return False
        if len(response.content) > MAX_RESPONSE_BYTES:
            logger.warning("The Azure voices list is too large; kept the old one.")
            return False
        voices = _parse(response.content)
        if voices is None:
            logger.warning("The Azure voices list is unusable; kept the old one.")
            return False
        data = json.dumps({"voices": [asdict(voice) for voice in voices]}, ensure_ascii=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".azure-voices.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.replace(tmp, path)
        tmp = None
        return True
    except (httpx2.HTTPError, OSError) as error:
        logger.warning("Could not refresh the Azure voices list: %s", type(error).__name__)
        return False
    finally:
        if tmp is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
        if http_client is None:
            client.close()
