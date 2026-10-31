import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import local_client
from fastapi.testclient import TestClient
from test_attempts_support import start_round
from test_backup_support import subject_with_history
from test_rounds_support import make_world
from test_topics_support import upgrade

from app.config import Settings
from app.db.session import SessionLocal
from app.features.backup import archive, cleanup, storage
from app.main import create_app

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def stamp(dt: datetime) -> str:
    return f"{dt.astimezone():%Y-%m-%d-%H%M%S}"


def touch(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"zip")
    return path


def pre_restore(settings: Settings, at: datetime) -> Path:
    return touch(settings.data_dir / "backups", f"answeroncue-pre-restore-{stamp(at)}.zip")


def an_export(settings: Settings, at: datetime) -> Path:
    return touch(settings.data_dir / "exports", f"answeroncue-history-{stamp(at)}.zip")


def names(folder: Path) -> set[str]:
    return {p.name for p in folder.iterdir()} if folder.is_dir() else set()


@pytest.fixture(autouse=True)
def fresh_throttle():
    cleanup._last_prune.clear()
    yield
    cleanup._last_prune.clear()


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


# --- prune -----------------------------------------------------------------------------------


def test_keeps_the_newest_pre_restore_backups_and_nothing_else(settings):
    backups = [pre_restore(settings, NOW - timedelta(days=n)) for n in range(7)]
    folder = settings.data_dir / "backups"
    others = {
        f"answeroncue-backup-{stamp(NOW - timedelta(days=30))}.zip",
        f"answeroncue-pre-restore-{stamp(NOW - timedelta(days=40))}.zip.part",
        ".snapshot-x.db",
        "notes.txt",
    }
    for name in others:
        touch(folder, name)

    pruned = cleanup.prune(settings, now=NOW)

    assert sorted(pruned.backups) == sorted(p.name for p in backups[5:])
    assert pruned.exports == []
    assert names(folder) == {p.name for p in backups[:5]} | others


def test_keep_zero_keeps_all_and_keep_one_keeps_the_newest(settings):
    backups = [pre_restore(settings, NOW - timedelta(days=n)) for n in range(3)]
    assert cleanup.prune(settings, now=NOW, keep=0).backups == []
    assert all(p.is_file() for p in backups)
    assert len(cleanup.prune(settings, now=NOW, keep=1).backups) == 2
    assert names(settings.data_dir / "backups") == {backups[0].name}


def test_exports_older_than_the_limit_are_deleted(settings):
    old = an_export(settings, NOW - timedelta(days=31))
    young = an_export(settings, NOW - timedelta(days=29))
    edge = an_export(settings, NOW - timedelta(days=30) + timedelta(minutes=1))
    part_name = f"answeroncue-history-{stamp(NOW - timedelta(days=90))}.zip.part"
    part = touch(settings.data_dir / "exports", part_name)

    assert cleanup.prune(settings, now=NOW, days=0).exports == []
    assert old.is_file()

    pruned = cleanup.prune(settings, now=NOW)
    assert pruned.exports == [old.name]
    assert names(settings.data_dir / "exports") == {young.name, edge.name, part.name}


def test_nothing_is_deleted_while_a_restore_holds_the_lock(settings):
    backups = [pre_restore(settings, NOW - timedelta(days=n)) for n in range(7)]
    old = an_export(settings, NOW - timedelta(days=40))
    assert archive._restore_lock.acquire(blocking=False)
    try:
        assert cleanup.prune(settings, now=NOW) == cleanup.Pruned(backups=[], exports=[])
        assert all(p.is_file() for p in [*backups, old])
    finally:
        archive._restore_lock.release()
    pruned = cleanup.prune(settings, now=NOW)
    assert len(pruned.backups) == 2 and pruned.exports == [old.name]


def test_an_unlink_error_skips_that_file_only(settings, monkeypatch, caplog):
    backups = [pre_restore(settings, NOW - timedelta(days=n)) for n in range(4)]
    stuck = backups[3]
    original = Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name == stuck.name:
            raise PermissionError("in use")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    with caplog.at_level(logging.WARNING, logger=cleanup.logger.name):
        pruned = cleanup.prune(settings, now=NOW, keep=1)
    assert sorted(pruned.backups) == sorted([backups[1].name, backups[2].name])
    assert names(settings.data_dir / "backups") == {backups[0].name, stuck.name}
    assert stuck.name in caplog.text


def test_prune_never_raises(settings, monkeypatch):
    pre_restore(settings, NOW)

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cleanup, "_files", broken)
    assert cleanup.prune(settings, now=NOW) == cleanup.Pruned(backups=[], exports=[])
    assert not archive._restore_lock.locked()


def test_missing_folders_are_fine(settings):
    assert cleanup.prune(settings, now=NOW) == cleanup.Pruned(backups=[], exports=[])


# --- prune_if_due ----------------------------------------------------------------------------


def test_prune_if_due_runs_at_most_once_a_day(settings):
    first = an_export(settings, NOW - timedelta(days=40))
    cleanup.prune_if_due(settings, NOW)
    assert not first.exists()

    second = an_export(settings, NOW - timedelta(days=41))
    cleanup.prune_if_due(settings, NOW + timedelta(hours=1))
    assert second.is_file()
    cleanup.prune_if_due(settings, NOW + timedelta(hours=25))
    assert not second.exists()


def test_prune_if_due_never_raises(settings, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cleanup, "prune", broken)
    cleanup.prune_if_due(settings, NOW)


def test_prune_if_due_does_nothing_when_both_limits_are_zero(settings, monkeypatch):
    calls = []
    monkeypatch.setattr(cleanup, "prune", lambda *a, **k: calls.append(1))
    cleanup.prune_if_due(settings.model_copy(update={"pre_restore_keep": 0, "export_keep_days": 0}))
    assert calls == []


# --- API -------------------------------------------------------------------------------------


def test_storage_shows_the_default_limits(client, db):
    body = client.get("/api/data/storage").json()
    assert (body["pre_restore_keep"], body["export_keep_days"]) == (5, 30)


def test_put_cleanup_saves_the_limits_and_prunes(client, db, settings):
    backups = [pre_restore(settings, NOW - timedelta(days=n)) for n in range(4)]
    an_export(settings, datetime.now(UTC) - timedelta(days=11))
    young = an_export(settings, datetime.now(UTC) - timedelta(days=9))

    response = client.put("/api/data/cleanup", json={"pre_restore_keep": 2, "export_keep_days": 10})

    assert response.status_code == 200, response.text
    assert response.json() == {
        "pre_restore_keep": 2,
        "export_keep_days": 10,
        "deleted_backups": 2,
        "deleted_exports": 1,
    }
    env = settings.env_file_path.read_text()
    assert "AOC_PRE_RESTORE_KEEP=2" in env and "AOC_EXPORT_KEEP_DAYS=10" in env
    assert names(settings.data_dir / "backups") == {backups[0].name, backups[1].name}
    assert names(settings.data_dir / "exports") == {young.name}
    body = client.get("/api/data/storage").json()
    assert (body["pre_restore_keep"], body["export_keep_days"]) == (2, 10)


def test_put_cleanup_is_guarded(settings, db):
    bare = TestClient(create_app(settings), base_url="http://127.0.0.1")
    response = bare.put("/api/data/cleanup", json={"pre_restore_keep": 2, "export_keep_days": 10})
    assert response.status_code == 403
    assert not settings.env_file_path.exists()


BACKUPS_RANGE = "Enter a number of backups from 0 to 100."
EXPORT_DAYS_RANGE = "Enter a number of days from 0 to 3650."


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"pre_restore_keep": 101, "export_keep_days": 10}, BACKUPS_RANGE),
        ({"pre_restore_keep": -1, "export_keep_days": 10}, BACKUPS_RANGE),
        ({"pre_restore_keep": 2, "export_keep_days": -1}, EXPORT_DAYS_RANGE),
        ({"pre_restore_keep": 2, "export_keep_days": 3651}, EXPORT_DAYS_RANGE),
    ],
)
def test_put_cleanup_refuses_out_of_range(client, db, settings, body, detail):
    backups = [pre_restore(settings, NOW - timedelta(days=n)) for n in range(7)]
    response = client.put("/api/data/cleanup", json=body)
    assert response.status_code == 422
    assert response.json()["detail"] == detail
    assert not settings.env_file_path.exists()
    assert all(p.is_file() for p in backups)


def test_round_start_prunes_once_a_day(client, db, settings, monkeypatch):
    world = make_world(db)
    old = an_export(settings, datetime.now(UTC) - timedelta(days=40))
    start_round(client, world)
    assert not old.exists()

    calls = []
    monkeypatch.setattr(cleanup, "prune", lambda *a, **k: calls.append(1))
    start_round(client, world)
    assert calls == []


def test_round_start_response_is_unchanged_when_prune_fails(client, db, monkeypatch):
    world = make_world(db)

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cleanup, "prune", broken)
    state = start_round(client, world)
    assert state["status"] == "active"


def test_export_and_backup_prune_afterwards(client, db, settings):
    subject_with_history(db, settings)
    old = an_export(settings, datetime.now(UTC) - timedelta(days=40))
    made = client.post("/api/data/exports")
    assert made.status_code == 201, made.text
    assert names(settings.data_dir / "exports") == {made.json()["name"]}
    assert not old.exists()

    backups = [pre_restore(settings, NOW - timedelta(days=n)) for n in range(6)]
    backup = client.post("/api/data/backups")
    assert backup.status_code == 201, backup.text
    left = names(settings.data_dir / "backups")
    assert backup.json()["name"] in left
    assert left - {backup.json()["name"]} == {p.name for p in backups[:5]}


def test_a_restore_keeps_its_own_pre_restore_backup(settings, db, monkeypatch):
    keep_one = settings.model_copy(update={"pre_restore_keep": 1})
    api = local_client(create_app(keep_one))
    subject_with_history(db, keep_one)
    manual = api.post("/api/data/backups")
    assert manual.status_code == 201, manual.text
    older = [pre_restore(keep_one, datetime.now(UTC) - timedelta(days=n)) for n in (3, 4, 5)]

    inner: list[cleanup.Pruned] = []
    original = archive._upgrade

    def upgrade_and_prune(db_path):
        inner.append(cleanup.prune(keep_one, now=datetime.now(UTC), keep=1))
        return original(db_path)

    monkeypatch.setattr(archive, "_upgrade", upgrade_and_prune)
    path = keep_one.data_dir / "backups" / manual.json()["name"]
    token = api.post("/api/data/restore", files={"file": (path.name, path.read_bytes())})
    assert token.status_code == 200, token.text
    result = api.post(f"/api/data/restore/{token.json()['token']}/apply")

    assert result.status_code == 200, result.text
    assert inner == [cleanup.Pruned(backups=[], exports=[])]
    assert all(not p.exists() for p in older)
    assert names(keep_one.data_dir / "backups") == {
        manual.json()["name"],
        result.json()["pre_restore_backup"],
    }


def test_storage_utcnow_is_the_prune_clock(client, db, settings, monkeypatch):
    monkeypatch.setattr(storage, "utcnow", lambda: NOW)
    old = an_export(settings, NOW - timedelta(days=31))
    young = an_export(settings, NOW - timedelta(days=1))
    assert (
        client.put(
            "/api/data/cleanup", json={"pre_restore_keep": 5, "export_keep_days": 30}
        ).json()["deleted_exports"]
        == 1
    )
    assert not old.exists() and young.is_file()
