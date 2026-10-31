import logging
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from test_attempts_support import (
    FakeProviders,
    answer_current,
    override_providers,
    post_answer,
    start_round,
    wav,
)
from test_backup_support import NOW, add_recording, subject_with_history
from test_rounds_support import make_world
from test_topics_support import BACKEND, upgrade

from app.config import Settings
from app.db.session import SessionLocal
from app.features.attempts.models import AttemptProcessing
from app.features.recording import retention
from app.features.recording.models import Recording
from app.features.rounds.models import Attempt


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def _cfg(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()
    return cfg


def _columns(url: str, table: str) -> set[str]:
    return {c["name"] for c in inspect(create_engine(url)).get_columns(table)}


def _aged(db, settings, *days: int) -> list[Recording]:
    return [add_recording(db, settings, age=timedelta(days=d)) for d in days]


def _ids(selection) -> set[int]:
    return set(selection.recording_ids)


def test_migration_adds_audio_deleted_at(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/m.db"
    cfg = _cfg(url)
    command.upgrade(cfg, "head")
    assert "audio_deleted_at" in _columns(url, "recording")
    assert not inspect(create_engine(url)).get_foreign_keys("recording")
    with create_engine(url).begin() as conn:
        conn.execute(
            text(
                "INSERT INTO recording (purpose, mime, sample_rate, duration_ms, size_bytes, "
                "stored_path) VALUES ('answer', 'audio/wav', 16000, 10, 10, 'recordings/1.wav')"
            )
        )
    command.downgrade(cfg, "0010_adaptive")
    assert "audio_deleted_at" not in _columns(url, "recording")
    with create_engine(url).connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM recording")).scalar_one() == 1
    command.upgrade(cfg, "head")
    assert "audio_deleted_at" in _columns(url, "recording")


def test_select_by_policy(db, settings):
    # unlinked recordings, so the 24 h grace is the only extra rule (1 day old passes it)
    young, middle, old = _aged(db, settings, 1, 40, 100)
    select = lambda policy: retention.select_recordings(  # noqa: E731
        db, scope="policy", policy=policy, now=NOW
    )
    assert _ids(select("30d")) == {middle.id, old.id}
    assert _ids(select("90d")) == {old.id}
    assert not select("forever").recording_ids
    assert _ids(select("assessed")) == {young.id, middle.id, old.id}
    selection = select("30d")
    assert selection.bytes == middle.size_bytes + old.size_bytes


def test_unresolved_answers_are_kept(db, settings):
    history = subject_with_history(db, settings)
    processing, failed = history["attempts"]
    processing.processing_status = "processing"
    failed.outcome = "technical_failure"
    row = db.query(AttemptProcessing).filter_by(attempt_id=failed.id).one()
    row.status, row.stage, row.failure_kind = "failed", "transcription", "transcription"
    db.commit()
    selection = retention.select_recordings(db, scope="all", now=NOW)
    kept = {r.id for r in history["recordings"]}
    assert not (_ids(selection) & kept)
    assert selection.kept_unresolved == 2
    assert history["mic"].id in _ids(selection)

    # an interrupted attempt's partial audio is selectable
    failed.outcome = "interrupted"
    failed.processing_status = None
    row.status, row.stage = "ready", "not_assessed"
    db.commit()
    selection = retention.select_recordings(db, scope="all", now=NOW)
    assert history["recordings"][1].id in _ids(selection)
    assert selection.kept_unresolved == 1


def test_unlinked_recordings_have_a_grace_day(db, settings):
    fresh = add_recording(db, settings, purpose="mic_check", age=timedelta(hours=2))
    stale = add_recording(db, settings, purpose="mic_check", age=timedelta(hours=30))
    for kwargs in ({"scope": "all"}, {"scope": "policy", "policy": "assessed"}):
        selection = retention.select_recordings(db, now=NOW, **kwargs)
        assert _ids(selection) == {stale.id}, kwargs
        assert fresh.id not in _ids(selection)


def test_delete_audio_keeps_scores(client, db, settings):
    history = subject_with_history(db, settings)
    round_id = history["round"].id
    before = client.get(f"/api/reports/rounds/{round_id}").json()
    rows_before = {a.id: (a.recording_id, a.outcome) for a in db.query(Attempt).all()}
    transcripts = {
        r.attempt_id: (r.transcript_text, r.metrics_json, r.assessment_json)
        for r in db.query(AttemptProcessing).all()
    }
    ids = [r.id for r in history["recordings"]]
    deleted, size, skipped = retention.delete_audio(db, settings, ids, NOW)
    assert (deleted, skipped) == (2, 0)
    assert size == sum(r.size_bytes for r in history["recordings"])
    for rec in history["recordings"]:
        assert not (settings.data_dir / rec.stored_path).exists()
        db.refresh(rec)
        assert rec.audio_deleted_at is not None and rec.duration_ms == 7500
        assert rec.vad_json == '{"engine": "test"}'
    db.expire_all()
    assert {a.id: (a.recording_id, a.outcome) for a in db.query(Attempt).all()} == rows_before
    assert {
        r.attempt_id: (r.transcript_text, r.metrics_json, r.assessment_json)
        for r in db.query(AttemptProcessing).all()
    } == transcripts
    assert client.get(f"/api/reports/rounds/{round_id}").json() == before


def test_file_in_use_is_skipped(db, settings, monkeypatch):
    first, second = _aged(db, settings, 5, 6)
    locked = settings.data_dir / second.stored_path
    original = Path.unlink

    def unlink(self, *args, **kwargs):
        if self == locked:
            raise PermissionError("in use")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    result = retention.delete_audio(db, settings, [first.id, second.id], NOW)
    assert result == (1, first.size_bytes, 1)
    db.refresh(first)
    db.refresh(second)
    assert first.audio_deleted_at is not None
    assert second.audio_deleted_at is None and locked.exists()


def test_sweep_is_throttled_and_never_raises(db, settings, caplog):
    settings.audio_retention = "30d"
    first = _aged(db, settings, 40)[0]
    retention.sweep_if_due(settings, NOW)
    db.refresh(first)
    assert first.audio_deleted_at is not None
    second = _aged(db, settings, 50)[0]
    retention.sweep_if_due(settings, NOW + timedelta(minutes=10))
    db.refresh(second)
    assert second.audio_deleted_at is None  # within the hour: no second pass
    retention.sweep_if_due(settings, NOW + timedelta(hours=2))
    db.refresh(second)
    assert second.audio_deleted_at is not None

    broken = Settings(
        _env_file=None,
        base_env_file=None,
        data_dir=settings.data_dir / "other",
        database_url="nonsense://nowhere/none",
        audio_retention="assessed",
    )
    with caplog.at_level(logging.WARNING):
        retention.sweep_if_due(broken, NOW)
    assert any("retention" in record.message.lower() for record in caplog.records)

    empty = Settings(
        _env_file=None,
        base_env_file=None,
        data_dir=settings.data_dir / "empty",
        audio_retention="assessed",
    )
    retention.sweep_if_due(empty, NOW)  # a database without tables: logged, not raised


def test_forever_never_sweeps(db, settings):
    old = _aged(db, settings, 400)[0]
    retention.sweep_if_due(settings, NOW)
    db.refresh(old)
    assert old.audio_deleted_at is None


def test_create_recording_runs_the_sweep_first(client, db, settings):
    settings.audio_retention = "assessed"
    history = subject_with_history(db, settings)
    old = history["recordings"][0]
    created = client.post(
        "/api/recordings",
        files={"file": ("a.wav", wav("speech_mid.wav"), "audio/wav")},
        data={"purpose": "answer"},
    )
    assert created.status_code == 201
    new_id = created.json()["id"]
    db.expire_all()
    assert db.get(Recording, old.id).audio_deleted_at is not None
    assert not (settings.data_dir / old.stored_path).exists()
    fresh = db.get(Recording, new_id)
    assert fresh.audio_deleted_at is None
    assert (settings.data_dir / fresh.stored_path).is_file()


def test_answer_upload_sweeps_without_warnings(client, db, settings, caplog):
    settings.audio_retention = "assessed"
    history = subject_with_history(db, settings, "Geografia")
    old = history["recordings"][0]
    world = make_world(db)
    override_providers(client.app, FakeProviders())
    round_id = start_round(client, world)["id"]
    attempt_id = answer_current(client, round_id)
    with caplog.at_level(logging.WARNING):
        events, audio = post_answer(client, attempt_id)
    assert events.status_code in (200, 201) and audio.status_code == 201, audio.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    db.expire_all()
    assert db.get(Recording, old.id).audio_deleted_at is not None
    uploaded = db.get(Attempt, attempt_id).recording_id
    assert uploaded is not None
    assert db.get(Recording, uploaded).audio_deleted_at is None


def test_deleted_audio_is_gone(client, db, settings):
    (recording,) = _aged(db, settings, 5)
    assert client.get(f"/api/recordings/{recording.id}/audio").status_code == 200
    retention.delete_audio(db, settings, [recording.id], NOW)
    response = client.get(f"/api/recordings/{recording.id}/audio")
    assert response.status_code == 410
    assert response.json()["detail"] == (
        "This recording was deleted (Settings → Storage and backup)."
    )
    assert client.post(f"/api/recordings/{recording.id}/analyze").status_code == 410


def test_fingerprint_is_stable_and_short():
    assert retention.fingerprint([3, 1, 2]) == retention.fingerprint([1, 2, 3])
    assert retention.fingerprint([1]) != retention.fingerprint([2])
    assert len(retention.fingerprint([])) == 16
