"""Voice downloads started from the browser: a thread per voice, polled for progress."""

import logging
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.config import Settings
from app.features.voice import download
from app.features.voice.download import DownloadError

logger = logging.getLogger(__name__)

UNEXPECTED_ERROR = "Could not download the voice. Try again."


class DownloadState(BaseModel):
    voice: str
    status: Literal["idle", "running", "done", "failed"]
    bytes_done: int
    bytes_total: int | None
    error: str | None


_lock = threading.Lock()
_states: dict[str, DownloadState] = {}


def _installed(voices_dir: Path, voice: str) -> bool:
    return (voices_dir / f"{voice}.onnx").is_file() and (
        voices_dir / f"{voice}.onnx.json"
    ).is_file()


def get_state(settings: Settings, voice: str) -> DownloadState:
    installed = _installed(settings.effective_voices_dir, voice)
    with _lock:
        state = _states.get(voice)
        if state is not None and (
            state.status in ("running", "failed") or (state.status == "done" and installed)
        ):
            return state.model_copy()
    status = "done" if installed else "idle"
    return DownloadState(voice=voice, status=status, bytes_done=0, bytes_total=None, error=None)


def _set(voice: str, **changes) -> None:
    with _lock:
        _states[voice] = _states[voice].model_copy(update=changes)


def _run(voice: str, dest_dir: Path) -> None:
    def progress(done: int, total: int | None) -> None:
        _set(voice, bytes_done=done, bytes_total=total)

    try:
        download.download_voice(voice, dest_dir, progress=progress)
    except DownloadError as error:
        _set(voice, status="failed", error=str(error))
    except Exception:
        logger.exception("Voice download %s failed", voice)
        _set(voice, status="failed", error=UNEXPECTED_ERROR)
    else:
        _set(voice, status="done", error=None)


def start(settings: Settings, voice: str) -> DownloadState:
    """Starts the download, or joins the one already running for this voice."""
    download.voice_urls(voice, settings.effective_voices_dir)  # DownloadError for an unknown voice
    with _lock:
        state = _states.get(voice)
        if state is not None and state.status == "running":
            return state.model_copy()
        state = DownloadState(
            voice=voice, status="running", bytes_done=0, bytes_total=None, error=None
        )
        _states[voice] = state
    thread = threading.Thread(
        target=_run, args=(voice, settings.effective_voices_dir), daemon=True, name=f"voice-{voice}"
    )
    thread.start()
    return state.model_copy()
