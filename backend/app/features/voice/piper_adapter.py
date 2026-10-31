"""Local, offline synthesis with Piper (piper-tts): {voices_dir}/{voice}.onnx + .onnx.json."""

import hashlib
import importlib.metadata
import io
import re
import threading
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.features.providers.types import (
    AudioResult,
    Provenance,
    ProviderError,
    Usage,
    VoiceSpec,
)
from app.features.voice.cache import wav_duration_ms

NOT_INSTALLED_MESSAGE = "The Piper voice is not installed. Run scripts/get-piper-voice.ps1."
SYNTHESIS_FAILED_MESSAGE = "Could not generate speech with the Piper voice."
VOICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")


def _default_loader(model_path: Path) -> Any:
    from piper import PiperVoice

    return PiperVoice.load(model_path)


def _package_version() -> str:
    try:
        return importlib.metadata.version("piper-tts")
    except importlib.metadata.PackageNotFoundError:
        return "missing"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PiperSynthesizer:
    def __init__(self, voices_dir: Path, *, loader: Callable[[Path], Any] | None = None) -> None:
        self.voices_dir = voices_dir
        self._loader = loader or _default_loader
        self.package_version = _package_version()
        self._lock = threading.Lock()
        # Keyed by (path, mtime, size), so a re-downloaded model is loaded and hashed again.
        self._voices: dict[tuple[Path, int, int], Any] = {}
        self._hashes: dict[tuple[Path, int, int], str] = {}

    def model_path(self, voice: str) -> Path | None:
        if not VOICE_ID_PATTERN.fullmatch(voice):
            return None
        return self.voices_dir / f"{voice}.onnx"

    def is_installed(self, voice: str) -> bool:
        path = self.model_path(voice)
        return path is not None and path.is_file() and path.with_name(path.name + ".json").is_file()

    def _model_id(self, voice: str) -> tuple[Path, int, int]:
        if not self.is_installed(voice):
            raise ProviderError("unavailable", "piper", NOT_INSTALLED_MESSAGE)
        path = self.model_path(voice)
        stat = path.stat()
        return path, stat.st_mtime_ns, stat.st_size

    def engine_version(self, voice: str) -> str:
        """Package version plus the model's sha256 (first 12 hex), for the cache key."""
        model_id = self._model_id(voice)
        with self._lock:
            if model_id not in self._hashes:
                self._hashes[model_id] = _file_sha256(model_id[0])[:12]
            return f"{self.package_version}+{self._hashes[model_id]}"

    def _load(self, voice: str) -> Any:
        model_id = self._model_id(voice)
        with self._lock:
            if model_id not in self._voices:
                try:
                    self._voices[model_id] = self._loader(model_id[0])
                except Exception:
                    # A truncated or corrupt download: same advice as a missing one.
                    raise ProviderError("unavailable", "piper", NOT_INSTALLED_MESSAGE) from None
            return self._voices[model_id]

    def synthesize(self, text: str, voice: VoiceSpec, *, operation_id: str) -> AudioResult:
        model = self._load(voice.voice)
        try:
            from piper import SynthesisConfig

            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav_file:
                model.synthesize_wav(
                    text, wav_file, syn_config=SynthesisConfig(length_scale=1 / voice.rate)
                )
            audio = buffer.getvalue()
        except Exception:
            raise ProviderError("server", "piper", SYNTHESIS_FAILED_MESSAGE) from None
        duration_ms = wav_duration_ms(audio) or 0
        return AudioResult(
            audio=audio,
            mime="audio/wav",
            usage=Usage(audio_seconds=duration_ms / 1000, characters=len(text)),
            provenance=Provenance(provider="piper", model=voice.voice, operation_id=operation_id),
        )
