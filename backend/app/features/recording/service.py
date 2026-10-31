import contextlib
import io
import wave
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import Settings
from app.features.recording import retention, vad
from app.features.recording.models import Recording
from app.features.recording.schemas import RecordingOut, VadResult

MAX_UPLOAD_BYTES = 30 * 1024 * 1024
SAMPLE_RATE = 16000
PURPOSES = {"mic_check", "answer"}

WRONG_FORMAT = "The recording must be in WAV format, 16 kHz, mono."
TOO_LARGE = "The recording is too large."
EMPTY = "The recording is empty."
NOT_FOUND = "Recording not found."
AUDIO_DELETED = "This recording was deleted (Settings → Storage and backup)."
INVALID_PURPOSE = "Invalid recording purpose."


@dataclass(frozen=True)
class WavInfo:
    samples: int
    sample_rate: int

    @property
    def duration_ms(self) -> int:
        return round(self.samples * 1000 / self.sample_rate)


def read_upload(file_obj) -> bytes:
    """Read at most the limit plus one byte, so an oversized upload is detected cheaply."""
    return file_obj.read(MAX_UPLOAD_BYTES + 1)


def read_pcm(data: bytes) -> tuple[WavInfo, bytes]:
    """Accept only RIFF/WAVE PCM 16-bit mono 16 kHz with at least one sample."""
    if not data:
        raise HTTPException(422, EMPTY)
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise HTTPException(415, WRONG_FORMAT)
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, SAMPLE_RATE):
                raise HTTPException(415, WRONG_FORMAT)
            # Count what is really there; a header can claim more than the file holds.
            pcm = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as exc:
        raise HTTPException(415, WRONG_FORMAT) from exc
    pcm = pcm[: len(pcm) - len(pcm) % 2]
    if not pcm:
        raise HTTPException(422, EMPTY)
    return WavInfo(samples=len(pcm) // 2, sample_rate=SAMPLE_RATE), pcm


def get_recording(session: Session, recording_id: int) -> Recording:
    recording = session.get(Recording, recording_id)
    if recording is None:
        raise HTTPException(404, NOT_FOUND)
    return recording


def audio_path(settings: Settings, recording: Recording) -> Path:
    if recording.audio_deleted_at is not None:
        raise HTTPException(410, AUDIO_DELETED)
    path = retention.data_file(settings, recording.stored_path)
    if path is None or not path.is_file():
        raise HTTPException(404, NOT_FOUND)
    return path


def _out(recording: Recording) -> RecordingOut:
    return RecordingOut(
        id=recording.id, duration_ms=recording.duration_ms, purpose=recording.purpose
    )


def create_recording(
    session: Session, settings: Settings, purpose: str, data: bytes
) -> RecordingOut:
    if purpose not in PURPOSES:
        raise HTTPException(422, INVALID_PURPOSE)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, TOO_LARGE)
    info, _ = read_pcm(data)
    # Before the new row exists, so the sweep can never touch this recording; it uses its own
    # short transaction and runs only while this session holds no uncommitted write.
    retention.sweep_if_due(settings)
    recording = Recording(
        purpose=purpose,
        mime="audio/wav",
        sample_rate=info.sample_rate,
        duration_ms=info.duration_ms,
        size_bytes=len(data),
        stored_path="",
    )
    session.add(recording)
    session.flush()
    relative = f"recordings/{recording.id}.wav"
    recording.stored_path = relative
    target = settings.data_dir / relative
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        session.commit()
    except BaseException:
        session.rollback()
        with contextlib.suppress(OSError):
            target.unlink(missing_ok=True)
        raise
    return _out(recording)


def analyze_recording(session: Session, settings: Settings, recording_id: int) -> VadResult:
    recording = get_recording(session, recording_id)
    _, pcm = read_pcm(audio_path(settings, recording).read_bytes())
    result = vad.analyze_pcm(pcm)
    recording.vad_json = result.model_dump_json()
    recording.vad_engine = result.engine
    session.commit()
    return result


def delete_recording(session: Session, settings: Settings, recording_id: int) -> None:
    recording = get_recording(session, recording_id)
    path = retention.data_file(settings, recording.stored_path)
    session.delete(recording)
    session.commit()
    if path is not None:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
