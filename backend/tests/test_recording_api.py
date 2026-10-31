import io
import json
import wave
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from app.config import Settings
from app.db.base import Base
from app.db.registry import import_all_models
from app.features.recording import service
from app.main import create_app

BACKEND = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "audio"
FIXTURE_INFO = json.loads((FIXTURES / "fixtures.json").read_text(encoding="utf-8"))


def upgrade(url: str) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()
    command.upgrade(cfg, "head")


@pytest.fixture
def recording_settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data", frontend_dist=tmp_path / "dist")


@pytest.fixture
def api(recording_settings) -> TestClient:
    upgrade(recording_settings.effective_database_url)
    return TestClient(create_app(recording_settings))


def make_wav(channels: int = 1, rate: int = 16000, frames: int = 1600, width: int = 2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00" * frames * channels * width)
    return buffer.getvalue()


def upload(api: TestClient, data: bytes, purpose: str = "mic_check", name: str = "a.wav"):
    return api.post(
        "/api/recordings",
        files={"file": (name, data, "audio/wav")},
        data={"purpose": purpose},
    )


def test_migration_creates_recording_table(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/t.db"
    upgrade(url)
    inspector = inspect(create_engine(url))
    assert "recording" in inspector.get_table_names()
    assert {c["name"] for c in inspector.get_columns("recording")} == {
        "id", "purpose", "mime", "sample_rate", "duration_ms", "size_bytes", "stored_path",
        "vad_json", "vad_engine", "created_at", "updated_at", "audio_deleted_at",
    }  # fmt: skip


def test_metadata_matches_migrations(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/m.db"
    upgrade(url)
    import_all_models()
    with create_engine(url).connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_upload_valid_wav_201_duration(api, recording_settings):
    data = (FIXTURES / "speech_mid.wav").read_bytes()
    response = upload(api, data, purpose="answer")
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"id", "duration_ms", "purpose"}
    assert body["purpose"] == "answer"
    expected = FIXTURE_INFO["files"]["speech_mid.wav"]["samples"] * 1000 / 16000
    assert abs(body["duration_ms"] - expected) <= 1
    stored = recording_settings.data_dir / "recordings" / f"{body['id']}.wav"
    assert stored.read_bytes() == data


def test_upload_rejects_non_wav_415(api):
    response = upload(api, b"%PDF-1.4 not audio at all", name="a.pdf")
    assert response.status_code == 415
    assert response.json()["detail"] == "The recording must be in WAV format, 16 kHz, mono."


def test_upload_rejects_stereo_415(api):
    response = upload(api, make_wav(channels=2))
    assert response.status_code == 415
    assert response.json()["detail"] == "The recording must be in WAV format, 16 kHz, mono."


def test_upload_rejects_44k_415(api):
    response = upload(api, make_wav(rate=44100))
    assert response.status_code == 415


def test_upload_rejects_8bit_415(api):
    response = upload(api, make_wav(width=1))
    assert response.status_code == 415


def test_upload_empty_422(api, recording_settings):
    response = upload(api, make_wav(frames=0))
    assert response.status_code == 422
    assert response.json()["detail"] == "The recording is empty."
    assert upload(api, b"").status_code == 422
    assert not list((recording_settings.data_dir / "recordings").glob("*"))


def test_upload_bad_purpose_422(api):
    response = upload(api, make_wav(), purpose="other")
    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid recording purpose."


def test_upload_too_large_413(api, monkeypatch):
    monkeypatch.setattr(service, "MAX_UPLOAD_BYTES", 1000)
    response = upload(api, make_wav(frames=1000))
    assert response.status_code == 413
    assert response.json()["detail"] == "The recording is too large."


def test_get_audio_and_404(api):
    data = make_wav(frames=3200)
    recording_id = upload(api, data).json()["id"]
    response = api.get(f"/api/recordings/{recording_id}/audio")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == data
    missing = api.get("/api/recordings/9999/audio")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Recording not found."


def test_delete_removes_file(api, recording_settings):
    recording_id = upload(api, make_wav()).json()["id"]
    stored = recording_settings.data_dir / "recordings" / f"{recording_id}.wav"
    assert stored.is_file()
    assert api.delete(f"/api/recordings/{recording_id}").status_code == 204
    assert not stored.exists()
    assert api.get(f"/api/recordings/{recording_id}/audio").status_code == 404
    assert api.delete(f"/api/recordings/{recording_id}").status_code == 404
