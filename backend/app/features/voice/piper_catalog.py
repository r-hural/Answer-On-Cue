"""The Piper voices per language: Hugging Face's voices.json, cached, with a pinned fallback.

Only refresh() goes to the network; everything else reads the cached file or PINNED.
"""

import contextlib
import json
import logging
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2

from app.features.languages.profiles import PROFILES, LanguageProfile
from app.features.voice.catalog import PIPER_VOICES
from app.features.voice.piper_adapter import VOICE_ID_PATTERN

logger = logging.getLogger(__name__)

VOICES_JSON_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json"
REPO_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
CATALOG_FILE = "voices.json"
CATALOG_MAX_AGE = timedelta(days=7)
MAX_CATALOG_BYTES = 5_000_000
OFFERED_QUALITIES = ("high", "medium")
TIMEOUT = httpx2.Timeout(30.0, connect=15.0)
# A relative path inside the repository: no "..", no leading slash, no query.
_MODEL_PATH = re.compile(r"^[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-][A-Za-z0-9_.\-]*)*\.onnx$")

# Tests replace this to send a refresh through a MockTransport.
http_client_factory: Callable[[], httpx2.Client | None] = lambda: None  # noqa: E731


@dataclass(frozen=True)
class PiperEntry:
    key: str
    prefix: str
    name: str
    quality: str
    num_speakers: int
    model_path: str  # the config is model_path + ".json"
    model_bytes: int | None


def _pinned(key: str, model_bytes: int) -> PiperEntry:
    prefix, name, quality = key.split("-")
    family = prefix.split("_")[0]
    return PiperEntry(
        key=key,
        prefix=prefix,
        name=name,
        quality=quality,
        num_speakers=1,
        model_path=f"{family}/{prefix}/{name}/{quality}/{key}.onnx",
        model_bytes=model_bytes,
    )


# Hugging Face's list on 2026-10-25 (repository commit 5041a7a4): every profile's default.
PINNED: dict[str, PiperEntry] = {
    entry.key: entry
    for entry in (
        _pinned("pl_PL-gosia-medium", 63201294),
        _pinned("pl_PL-darkman-medium", 63201294),
        _pinned("pl_PL-mc_speech-medium", 63201294),
        _pinned("pl_PL-bass-high", 114204024),
        _pinned("en_GB-cori-high", 114219352),
        _pinned("de_DE-thorsten-high", 113895201),
        _pinned("fr_FR-siwis-medium", 63201294),
        _pinned("es_ES-davefx-medium", 63201294),
        _pinned("it_IT-serena-high", 114204024),
        _pinned("uk_UA-tetiana-high", 114204024),
    )
}

_PREFIXES = frozenset(profile.piper_prefix for profile in PROFILES.values())
# The parsed cache per file, reused while its mtime and size stay the same.
_loaded: dict[Path, tuple[tuple[int, int], dict[str, PiperEntry]]] = {}


def _entry(key: object, raw: object) -> PiperEntry | None:
    """One voices.json entry, or None when it is malformed or names an unsafe path."""
    if not isinstance(key, str) or not VOICE_ID_PATTERN.fullmatch(key) or not isinstance(raw, dict):
        return None
    try:
        prefix = raw["language"]["code"]
        name, quality, files = raw["name"], raw["quality"], raw["files"]
        speakers = int(raw.get("num_speakers", 1))
        model_path = next(path for path in files if str(path).endswith(".onnx"))
        size = files[model_path].get("size_bytes") if isinstance(files[model_path], dict) else None
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration):
        return None
    if not all(isinstance(value, str) for value in (prefix, name, quality, model_path)):
        return None
    if not _MODEL_PATH.fullmatch(model_path) or not model_path.endswith(f"/{key}.onnx"):
        return None
    return PiperEntry(
        key=key,
        prefix=prefix,
        name=name,
        quality=quality,
        num_speakers=speakers,
        model_path=model_path,
        model_bytes=size if isinstance(size, int) else None,
    )


def _parse(data: bytes) -> dict[str, PiperEntry] | None:
    """The supported languages' entries of a voices.json; None unless it is usable."""
    if len(data) > MAX_CATALOG_BYTES:
        return None
    try:
        raw = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    entries = {}
    for key, value in raw.items():
        entry = _entry(key, value)
        if entry is not None and entry.prefix in _PREFIXES:
            entries[entry.key] = entry
    if any(profile.default_piper_voice not in entries for profile in PROFILES.values()):
        return None
    return entries


def load_catalog(voices_dir: Path) -> dict[str, PiperEntry]:
    """The cached voices.json when it is valid, else PINNED. Never the network."""
    path = voices_dir / CATALOG_FILE
    try:
        stat = path.stat()
    except OSError:
        return PINNED
    if stat.st_size > MAX_CATALOG_BYTES:
        return PINNED
    identity = (stat.st_mtime_ns, stat.st_size)
    known = _loaded.get(path)
    if known is not None and known[0] == identity:
        return known[1]
    try:
        entries = _parse(path.read_bytes())
    except OSError:
        return PINNED
    if entries is None:
        logger.warning("The cached Piper voices list is invalid; the pinned list is used.")
        return PINNED
    _loaded[path] = (identity, entries)
    return entries


def _fresh(path: Path, now: datetime) -> bool:
    try:
        changed = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError:
        return False
    return now - changed < CATALOG_MAX_AGE


def _download(client: httpx2.Client) -> bytes | None:
    with client.stream("GET", VOICES_JSON_URL, follow_redirects=True) as response:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_bytes():
            body += chunk
            if len(body) > MAX_CATALOG_BYTES:
                return None
    return bytes(body)


def refresh(
    voices_dir: Path, *, now: datetime | None = None, http_client: httpx2.Client | None = None
) -> bool:
    """Fetches voices.json when the cache is missing or older than CATALOG_MAX_AGE.

    True when a new file was written. A failure keeps the old file and is only logged."""
    path = voices_dir / CATALOG_FILE
    if _fresh(path, now or datetime.now(UTC)):
        return False
    http_client = http_client or http_client_factory()
    client = http_client or httpx2.Client(timeout=TIMEOUT, follow_redirects=True)
    tmp: str | None = None
    try:
        body = _download(client)
        if body is None or _parse(body) is None:
            logger.warning("The Piper voices list from Hugging Face is unusable; kept the old one.")
            return False
        voices_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".voices.", suffix=".tmp", dir=voices_dir)
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
        os.replace(tmp, path)
        tmp = None
        return True
    except (httpx2.HTTPError, OSError) as error:
        logger.warning("Could not refresh the Piper voices list: %s", type(error).__name__)
        return False
    finally:
        if tmp is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
        if http_client is None:
            client.close()


def offered(catalog: dict[str, PiperEntry], profile: LanguageProfile) -> list[PiperEntry]:
    """The language's single-speaker voices of high or medium quality.

    Polish keeps the catalog.PIPER_VOICES order; another language lists its default first.
    The rest follow by id."""
    entries = {
        key: entry
        for key, entry in catalog.items()
        if entry.prefix == profile.piper_prefix
        and entry.quality in OFFERED_QUALITIES
        and entry.num_speakers == 1
    }
    first = list(PIPER_VOICES) if profile.code == "pl" else [profile.default_piper_voice]
    order = [key for key in first if key in entries]
    order += sorted(key for key in entries if key not in order)
    return [entries[key] for key in order]


def label(entry: PiperEntry) -> str:
    if entry.key in PIPER_VOICES:
        return PIPER_VOICES[entry.key][0]
    name = " ".join(word.capitalize() for word in entry.name.replace("_", " ").split())
    quality = ", high quality" if entry.quality == "high" else ""
    return f"{name} (Piper, local{quality})"
