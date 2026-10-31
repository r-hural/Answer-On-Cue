"""Downloads Piper voice models from Hugging Face into the voices folder.

Usage: python -m app.features.voice.download [voice ...]   (default: every known voice)
"""

import contextlib
import json
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import httpx2

from app.features.voice.catalog import PIPER_VOICES
from app.features.voice.piper_catalog import PINNED, REPO_URL, load_catalog

KNOWN_VOICES: tuple[str, ...] = tuple(PIPER_VOICES)
MIN_MODEL_BYTES = 1_000_000
TIMEOUT = httpx2.Timeout(120.0, connect=15.0)

# Tests replace this to send a download through a MockTransport.
http_client_factory: Callable[[], httpx2.Client | None] = lambda: None  # noqa: E731

Progress = Callable[[int, int | None], None]


class _Counter:
    """Bytes read over both files; the total is known once every response has said its size."""

    def __init__(self, progress: Progress | None) -> None:
        self.progress = progress
        self.done = 0
        self.sizes: list[int | None] = []

    def start_file(self, length: int | None) -> None:
        self.sizes.append(length)
        self.report()

    def add(self, count: int) -> None:
        self.done += count
        self.report()

    def report(self) -> None:
        if self.progress is None:
            return
        total = None if any(size is None for size in self.sizes) else sum(self.sizes)
        self.progress(self.done, total)


class DownloadError(Exception):
    pass


def voice_urls(voice: str, voices_dir: Path | None = None) -> tuple[str, str]:
    """The .onnx and .onnx.json URLs, from the file paths of the folder's cached voices list
    (the pinned list without a folder)."""
    entry = (PINNED if voices_dir is None else load_catalog(voices_dir)).get(voice)
    if entry is None:
        raise DownloadError(f"Unknown Piper voice: {voice}.")
    model = f"{REPO_URL}/{entry.model_path}"
    return model, model + ".json"


def _fetch(client: httpx2.Client, url: str, dest_dir: Path, counter: _Counter) -> Path:
    fd, tmp = tempfile.mkstemp(prefix=".download.", suffix=".tmp", dir=dest_dir)
    try:
        with (
            os.fdopen(fd, "wb") as handle,
            client.stream("GET", url, follow_redirects=True) as response,
        ):
            response.raise_for_status()
            length = response.headers.get("content-length")
            counter.start_file(int(length) if length and length.isdigit() else None)
            for chunk in response.iter_bytes():
                handle.write(chunk)
                counter.add(len(chunk))
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return Path(tmp)


def download_voice(
    voice: str,
    dest_dir: Path,
    *,
    http_client: httpx2.Client | None = None,
    progress: Progress | None = None,
) -> Path:
    """Both files go to temp files and are checked; only then do they replace the old ones."""
    model_url, config_url = voice_urls(voice, dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    http_client = http_client or http_client_factory()
    client = http_client or httpx2.Client(timeout=TIMEOUT, follow_redirects=True)
    counter = _Counter(progress)
    temps: list[Path] = []
    try:
        try:
            temps.append(model_tmp := _fetch(client, model_url, dest_dir, counter))
            temps.append(config_tmp := _fetch(client, config_url, dest_dir, counter))
        except httpx2.HTTPStatusError as error:
            raise DownloadError(
                f"Could not download the voice {voice}: the server replied "
                f"with code {error.response.status_code}."
            ) from None
        except httpx2.HTTPError:
            raise DownloadError(
                f"Could not download the voice {voice}: no connection to Hugging Face."
            ) from None
        if model_tmp.stat().st_size <= MIN_MODEL_BYTES:
            raise DownloadError(f"The downloaded model for the voice {voice} is incomplete.")
        try:
            json.loads(config_tmp.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise DownloadError(
                f"The downloaded configuration for the voice {voice} is corrupt."
            ) from None
        model_path = dest_dir / f"{voice}.onnx"
        os.replace(model_tmp, model_path)
        os.replace(config_tmp, dest_dir / f"{voice}.onnx.json")
        return model_path
    finally:
        for tmp in temps:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
        if http_client is None:
            client.close()


def main(argv: list[str] | None = None, *, dest_dir: Path | None = None) -> int:
    with contextlib.suppress(AttributeError):
        sys.stdout.reconfigure(errors="replace")
    voices = (sys.argv[1:] if argv is None else argv) or list(KNOWN_VOICES)
    if dest_dir is None:
        from app.config import get_settings

        dest_dir = get_settings().effective_voices_dir
    failed = 0
    for voice in voices:
        print(f"Downloading the voice {voice} ...", flush=True)
        try:
            path = download_voice(voice, dest_dir)
        except DownloadError as error:
            failed += 1
            print(str(error), flush=True)
            continue
        print(f"Done: {path}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
