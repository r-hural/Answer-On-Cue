"""File-based voice cache: {key}.wav plus a {key}.json sidecar, no database."""

import contextlib
import hashlib
import io
import json
import os
import re
import tempfile
import unicodedata
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")
KEY_VERSION = "v1"


def normalize_text(text: str) -> str:
    """The only change ever made to question text: NFC and surrounding whitespace."""
    return unicodedata.normalize("NFC", text).strip()


def cache_key(provider: str, voice: str, rate: float, engine_version: str, text: str) -> str:
    raw = "|".join(
        (KEY_VERSION, provider, voice, f"{rate:.2f}", engine_version, normalize_text(text))
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_valid_key(key: str) -> bool:
    return bool(KEY_PATTERN.fullmatch(key))


def wav_duration_ms(audio: bytes) -> int | None:
    try:
        with wave.open(io.BytesIO(audio)) as wav:
            rate = wav.getframerate()
            return round(wav.getnframes() * 1000 / rate) if rate else None
    except (wave.Error, EOFError):
        return None


def _atomic_write(path: Path, data: bytes) -> None:
    """Temp file in the same folder, then os.replace: readers never see a half-written file."""
    fd, tmp = tempfile.mkstemp(prefix=f".{path.stem[:12]}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        try:
            os.replace(tmp, path)
        except PermissionError:
            # Windows refuses to replace a file that is open (being served or written by
            # another request). The same key means the same content, so keep the existing one.
            if not path.exists():
                raise
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


class VoiceCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, key: str) -> Path:
        if not is_valid_key(key):
            raise ValueError("invalid voice cache key")
        return self.root / f"{key}.wav"

    def _meta_path(self, key: str) -> Path:
        return self.path(key).with_suffix(".json")

    def get(self, key: str) -> Path | None:
        if not is_valid_key(key):
            return None
        path = self.path(key)
        return path if path.is_file() else None

    def read_meta(self, key: str) -> dict[str, Any] | None:
        if not is_valid_key(key):
            return None
        try:
            return json.loads(self._meta_path(key).read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None

    def put(self, key: str, audio: bytes, meta: dict[str, Any]) -> Path:
        path = self.path(key)
        self.root.mkdir(parents=True, exist_ok=True)
        sidecar = {**meta, "created_at": datetime.now(UTC).isoformat()}
        # Sidecar first: whenever the .wav exists, its description exists too.
        _atomic_write(
            self._meta_path(key), json.dumps(sidecar, ensure_ascii=False, indent=2).encode()
        )
        _atomic_write(path, audio)
        return path
