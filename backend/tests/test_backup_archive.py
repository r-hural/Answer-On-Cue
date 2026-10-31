import errno
import hashlib
import json
import os
import shutil
import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from test_backup_support import subject_with_history
from test_coverage_support import make_answer, make_round
from test_topics_support import BACKEND, upgrade

from app.config import Settings
from app.db.base import Base
from app.db.registry import import_all_models
from app.db.session import SessionLocal
from app.features.attempts import pipeline
from app.features.backup import archive
from app.features.providers.types import Usage
from app.features.providers.usage import DurableUsageRecorder, InMemoryUsageRecorder, UsageEvent
from app.features.recording.models import Recording
from app.main import create_app

OPENAI_KEY = "sk-test-SECRET-0123456789"
ANTHROPIC_KEY = "sk-ant-SECRET-abcdef"
KEYS = (OPENAI_KEY, ANTHROPIC_KEY)


@pytest.fixture
def secret_settings(settings) -> Settings:
    text_ = f"AOC_OPENAI_API_KEY={OPENAI_KEY}\nAOC_ANTHROPIC_API_KEY={ANTHROPIC_KEY}\n"
    settings.env_file_path.write_text(text_)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "settings.env").write_text(text_)
    return settings


@pytest.fixture
def db(secret_settings):
    upgrade(secret_settings.effective_database_url)
    with SessionLocal(secret_settings.effective_database_url) as session:
        yield session


@pytest.fixture
def api(secret_settings) -> TestClient:
    from conftest import local_client

    return local_client(create_app(secret_settings))


@pytest.fixture
def history(db, secret_settings) -> dict:
    data = subject_with_history(db, secret_settings)
    root = secret_settings.data_dir
    for stray in ("voices/model.onnx", "voice-cache/a.wav", "logs/app.log", "exports/e.zip"):
        (root / stray).parent.mkdir(parents=True, exist_ok=True)
        (root / stray).write_bytes(f"stray {OPENAI_KEY}".encode())
    (root / "backups").mkdir(exist_ok=True)
    (root / "documents" / "settings.env").write_text(f"AOC_OPENAI_API_KEY={OPENAI_KEY}")
    return data


def make_backup(api) -> dict:
    response = api.post("/api/data/backups")
    assert response.status_code == 201, response.text
    return response.json()


def backup_path(settings: Settings, backup: dict) -> Path:
    return settings.data_dir / "backups" / backup["name"]


def upload(api, path: Path):
    return api.post("/api/data/restore", files={"file": (path.name, path.read_bytes())})


def apply(api, token: str):
    return api.post(f"/api/data/restore/{token}/apply")


def head_revision() -> str:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist() if not i.is_dir()}


def write_zip(path: Path, members: dict[str, bytes], manifest: dict | None = None, *,
              symlinks=(), encrypted=()) -> Path:  # fmt: skip
    """A zip from members; the manifest (given, or the original patched) is written last.

    Members named in `symlinks` are stored as symbolic links (Unix mode in external_attr), and
    members in `encrypted` carry the "encrypted" flag in the central directory (zipfile resets
    the flag when it writes a member, so it is set afterwards; the central directory, which a
    reader lists, is written on close)."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if name in symlinks:
                info.external_attr = 0o120777 << 16
            zf.writestr(info, data)
            if name in encrypted:
                zf.getinfo(name).flag_bits |= 0x1
        if manifest is not None:
            zf.writestr("manifest.json", json.dumps(manifest))
    return path


def variant(source: Path, target: Path, *, drop=(), add=None, replace=None, patch=None,
            rehash=False, extra_files=(), symlinks=(), encrypted=()) -> Path:  # fmt: skip
    """A copy of a backup with members dropped, added or replaced, and its manifest patched.

    `rehash` recomputes the manifest's sha256 values, so only the changed content is wrong."""
    members = read_members(source)
    manifest = json.loads(members.pop("manifest.json"))
    if "manifest.json" in drop:
        return write_zip(target, members)
    for name in drop:
        members.pop(name, None)
        manifest["files"] = [f for f in manifest["files"] if f["path"] != name]
    members.update(replace or {})
    members.update(add or {})
    if rehash:
        manifest["files"] = [
            {"path": n, "bytes": len(d), "sha256": sha(d)}
            for n, d in members.items()
            if not n.startswith("..") and not n.startswith("/") and ":" not in n
        ]
    for entry in extra_files:
        manifest["files"].append(entry)
    manifest.update(patch or {})
    return write_zip(target, members, manifest, symlinks=symlinks, encrypted=encrypted)


def tree(root: Path) -> dict[str, int]:
    return {
        p.relative_to(root).as_posix(): p.stat().st_size
        for p in sorted(root.rglob("*"))
        if p.is_file() and "restore" not in p.relative_to(root).parts
    }


def snapshot(api, history) -> dict:
    sid = history["subject"].id
    return {
        "history": api.get("/api/reports/history", params={"subject_id": sid}).json(),
        "round": api.get(f"/api/reports/rounds/{history['round'].id}").json(),
        "coverage": api.get(f"/api/coverage/subjects/{sid}").json(),
        "subjects": api.get("/api/subjects").json(),
    }


def data_files(settings: Settings, history: dict) -> dict[str, bytes]:
    names = [r.stored_path for r in (*history["recordings"], history["mic"])]
    names.append(history["version"].stored_path)
    return {n: (settings.data_dir / n).read_bytes() for n in names}


# --- the backup ------------------------------------------------------------------------------


def test_backup_contents(api, db, secret_settings, history):
    body = make_backup(api)
    path = backup_path(secret_settings, body)
    assert path.is_file() and body["bytes"] == path.stat().st_size
    assert body["name"].startswith("answeroncue-backup-") and body["name"].endswith(".zip")
    assert body["counts"]["subjects"] == 1 and body["counts"]["recordings"] == 3
    assert body["counts"]["answers"] == 2 and body["counts"]["rounds"] == 1

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        infos = {i.filename: i for i in zf.infolist()}
    assert {"manifest.json", "answeroncue.db"} <= set(names)
    assert history["version"].stored_path in names
    for recording in (*history["recordings"], history["mic"]):
        assert recording.stored_path in names
    for name in names:
        assert Path(name).name not in ("settings.env", ".env")
        assert not name.startswith(("voices/", "voice-cache/", "logs/", "backups/", "exports/"))
        assert (
            name == "manifest.json"
            or name == "answeroncue.db"
            or name.startswith(("documents/", "recordings/"))
        )
    assert "documents/settings.env" not in names
    assert infos[history["recordings"][0].stored_path].compress_type == zipfile.ZIP_STORED
    assert infos["answeroncue.db"].compress_type == zipfile.ZIP_DEFLATED

    members = read_members(path)
    manifest = json.loads(members.pop("manifest.json"))
    assert manifest["format"] == archive.BACKUP_FORMAT
    assert manifest["format_version"] == archive.FORMAT_VERSION
    assert manifest["alembic_revision"] == head_revision()
    assert manifest["counts"] == body["counts"]
    assert {f["path"] for f in manifest["files"]} == set(members)
    for entry in manifest["files"]:
        assert (entry["bytes"], entry["sha256"]) == (
            len(members[entry["path"]]),
            sha(members[entry["path"]]),
        )
    assert not list((secret_settings.data_dir / "backups").glob("*.part"))


def test_backup_database_is_a_consistent_snapshot(api, db, secret_settings, history):
    path = backup_path(secret_settings, make_backup(api))
    snapshot_db = secret_settings.data_dir / "snapshot.db"
    snapshot_db.write_bytes(read_members(path)["answeroncue.db"])
    conn = sqlite3.connect(snapshot_db)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert conn.execute("SELECT count(*) FROM subject").fetchone() == (1,)
    finally:
        conn.close()


def test_backup_has_no_secret(api, db, secret_settings, history):
    path = backup_path(secret_settings, make_backup(api))
    for name, data in read_members(path).items():
        for key in KEYS:
            assert key.encode() not in data, name
    raw = path.read_bytes()
    for key in KEYS:
        assert key.encode() not in raw


def test_download_guard_and_name(api, db, secret_settings, history):
    body = make_backup(api)
    ok = api.get(f"/api/data/backups/{body['name']}")
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "application/zip"
    assert ok.content == backup_path(secret_settings, body).read_bytes()
    foreign = TestClient(create_app(secret_settings), base_url="http://evil.example")
    assert foreign.get(f"/api/data/backups/{body['name']}").status_code == 403
    for bad in ("..", "x/../../etc", "..%2F..%2Fsettings.env", "answeroncue-backup-1.zip", "a.zip"):
        assert api.get(f"/api/data/backups/{bad}").status_code == 404, bad
    stamp = datetime(2001, 1, 1)
    missing = f"answeroncue-backup-{stamp:%Y-%m-%d-%H%M%S}.zip"
    assert api.get(f"/api/data/backups/{missing}").status_code == 404


def test_changes_are_guarded(secret_settings, db):
    bare = TestClient(create_app(secret_settings))
    assert bare.post("/api/data/backups").status_code == 403
    assert bare.post("/api/data/restore", files={"file": ("a.zip", b"x")}).status_code == 403
    assert bare.post(f"/api/data/restore/{'a' * 32}/apply").status_code == 403
    assert bare.delete(f"/api/data/restore/{'a' * 32}").status_code == 403


def test_only_sqlite_is_supported(tmp_path):
    other = Settings(
        _env_file=None,
        base_env_file=None,
        data_dir=tmp_path / "data",
        database_url="postgresql://user@localhost/aoc",
    )
    with pytest.raises(Exception) as caught:
        archive.create_backup(None, other, now=datetime.now(UTC))
    assert caught.value.status_code == 422 and caught.value.detail == archive.SQLITE_ONLY


# --- validation ------------------------------------------------------------------------------


@pytest.fixture
def good(api, db, secret_settings, history, tmp_path) -> Path:
    source = backup_path(secret_settings, make_backup(api))
    kept = tmp_path / "good.zip"
    shutil.copyfile(source, kept)
    return kept


def test_validation_rejects(api, db, secret_settings, history, good, tmp_path):
    recording = history["recordings"][0].stored_path
    original = read_members(good)
    database = original["answeroncue.db"]
    broken_db = bytearray(database)
    for offset in range(4096 * 2, min(len(broken_db), 4096 * 4)):
        broken_db[offset] = 0xFF
    (tmp_path / "x.txt").write_text("this is not a zip")
    cases = {
        "not a zip": (tmp_path / "x.txt", archive.NOT_A_BACKUP),
        "no manifest": (variant(good, tmp_path / "a.zip", drop=("manifest.json",)),
                        archive.NOT_A_BACKUP),
        "wrong format": (variant(good, tmp_path / "b.zip", patch={"format": "other"}),
                         archive.NOT_A_BACKUP),
        "dot dot": (variant(good, tmp_path / "c.zip", add={"../evil.txt": b"x"}, rehash=True),
                    archive.UNEXPECTED_FILE),
        "absolute": (variant(good, tmp_path / "d.zip", add={"/abs.txt": b"x"}, rehash=True),
                     archive.UNEXPECTED_FILE),
        "drive": (variant(good, tmp_path / "e.zip", add={"C:/evil.txt": b"x"}, rehash=True),
                  archive.UNEXPECTED_FILE),
        "backslash": (variant(good, tmp_path / "f.zip", add={"documents\\..\\x": b"x"},
                              rehash=True), archive.UNEXPECTED_FILE),
        "settings.env": (variant(good, tmp_path / "g.zip", add={"settings.env": b"k"},
                                 rehash=True), archive.UNEXPECTED_FILE),
        "nested env": (variant(good, tmp_path / "h.zip", add={"documents/.env": b"k"},
                               rehash=True), archive.UNEXPECTED_FILE),
        "unlisted member": (variant(good, tmp_path / "i.zip", add={"documents/extra.txt": b"x"}),
                            archive.DAMAGED),
        "missing member": (variant(good, tmp_path / "j.zip", drop=(recording,),
                                   extra_files=({"path": recording, "bytes": 1,
                                                 "sha256": "0" * 64},)), archive.DAMAGED),
        "sha mismatch": (variant(good, tmp_path / "k.zip", replace={recording: b"RIFFxxxx"}),
                         archive.DAMAGED),
        "bad database": (variant(good, tmp_path / "l.zip",
                                 replace={"answeroncue.db": bytes(broken_db)}, rehash=True),
                         archive.DAMAGED),
        "future revision": (variant(good, tmp_path / "m.zip",
                                    patch={"alembic_revision": "9999_future"}),
                            archive.NEWER_VERSION),
        "future format": (variant(good, tmp_path / "n.zip", patch={"format_version": 99}),
                          archive.NEWER_VERSION),
        "symlink member": (variant(good, tmp_path / "o.zip",
                                   add={"documents/link": b"../../settings.env"}, rehash=True,
                                   symlinks=("documents/link",)), archive.UNEXPECTED_FILE),
        "encrypted member": (variant(good, tmp_path / "p.zip", encrypted=(recording,)),
                             archive.DAMAGED),
    }  # fmt: skip
    before_tree = tree(secret_settings.data_dir)
    before_counts = api.get("/api/subjects").json()
    for label, (path, message) in cases.items():
        response = upload(api, path)
        assert response.status_code == 422, (label, response.text)
        assert response.json()["detail"] == message, label
        assert tree(secret_settings.data_dir) == before_tree, label
        assert not list((secret_settings.data_dir / "restore").glob("*")), label
    assert api.get("/api/subjects").json() == before_counts


def test_preview_counts_and_newer_rounds(api, db, secret_settings, history, good):
    body = json.loads(read_members(good)["manifest.json"])
    later = datetime.now(UTC) + timedelta(minutes=5)
    make_round(db, history["subject"].id, at=later)
    response = upload(api, good)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert len(preview["token"]) == 32
    assert preview["counts"] == body["counts"]
    assert preview["current"]["rounds"] == body["counts"]["rounds"] + 1
    assert preview["newer_rounds"] == 1
    assert preview["app_version"] == body["app_version"]
    assert datetime.fromisoformat(preview["created_at"]) == datetime.fromisoformat(
        body["created_at"]
    )


def test_discard_staged(api, db, secret_settings, history, good):
    token = upload(api, good).json()["token"]
    staging = secret_settings.data_dir / "restore" / token
    assert staging.is_dir()
    assert api.delete(f"/api/data/restore/{token}").status_code == 204
    assert not staging.exists()
    assert apply(api, token).status_code == 404


# --- applying --------------------------------------------------------------------------------


def test_round_trip_reproduces_history(api, db, secret_settings, history):
    before = snapshot(api, history)
    files = data_files(secret_settings, history)
    backup = make_backup(api)

    created = api.post("/api/subjects", json={"name": "Nowy przedmiot", "language": "pl"})
    assert created.status_code == 201
    assert api.delete(f"/api/recordings/{history['recordings'][0].id}").status_code == 204
    assert snapshot(api, history) != before

    token = upload(api, backup_path(secret_settings, backup)).json()["token"]
    result = apply(api, token)
    assert result.status_code == 200, result.text
    assert snapshot(api, history) == before  # through the same client, after the swap
    assert data_files(secret_settings, history) == files
    pre = secret_settings.data_dir / "backups" / result.json()["pre_restore_backup"]
    assert pre.is_file() and pre.name.startswith("answeroncue-pre-restore-")
    assert result.json()["restored"] == backup["counts"]
    assert not (secret_settings.data_dir / "restore" / token).exists()
    # the pre-restore backup holds what was there before, the new subject included
    names = {s["name"] for s in json.loads(_subjects_in(pre))}
    assert "Nowy przedmiot" in names


def _subjects_in(zip_path: Path) -> str:
    target = zip_path.parent / "pre-check.db"
    target.write_bytes(read_members(zip_path)["answeroncue.db"])
    conn = sqlite3.connect(target)
    try:
        rows = conn.execute("SELECT name FROM subject").fetchall()
    finally:
        conn.close()
        target.unlink()
    return json.dumps([{"name": r[0]} for r in rows])


def test_restore_refuses_when_busy(api, db, secret_settings, history, good, monkeypatch):
    token = upload(api, good).json()["token"]
    active = make_round(db, history["subject"].id, at=datetime.now(UTC), status="active")
    busy = apply(api, token)
    assert busy.status_code == 409 and busy.json()["detail"] == archive.BUSY
    active.status = "paused"  # a paused round is fine to restore over, so it is not busy
    attempt = make_answer(
        db, history["round"], history["questions"][0], at=datetime.now(UTC),
        processing="processing",
    )  # fmt: skip
    db.commit()
    monkeypatch.setattr(pipeline, "_active", {attempt.id})
    again = apply(api, token)
    assert again.status_code == 409 and again.json()["detail"] == archive.BUSY
    monkeypatch.setattr(pipeline, "_active", set())
    assert apply(api, token).status_code == 200


def test_failed_swap_rolls_back(api, db, secret_settings, history, good, monkeypatch):
    backup = make_backup(api)
    api.post("/api/subjects", json={"name": "Po kopii", "language": "pl"})
    assert api.delete(f"/api/recordings/{history['recordings'][1].id}").status_code == 204
    expected = snapshot(api, history)
    present = tree(secret_settings.data_dir)
    token = upload(api, backup_path(secret_settings, backup)).json()["token"]
    real_replace = archive.os.replace

    def failing(src, dst, *args, **kwargs):
        if Path(src).parent.name == "new":  # moving a staged folder into place
            raise OSError("simulated failure")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(archive.os, "replace", failing)
    response = apply(api, token)
    monkeypatch.undo()
    assert response.status_code == 500
    assert response.json()["detail"] == archive.RESTORE_FAILED
    assert snapshot(api, history) == expected
    after = tree(secret_settings.data_dir)
    assert {k: v for k, v in after.items() if not k.startswith("backups/")} == {
        k: v for k, v in present.items() if not k.startswith("backups/")
    }
    assert any(n.startswith("backups/answeroncue-pre-restore-") for n in after)


def test_older_backup_is_upgraded(api, db, secret_settings, history, good, tmp_path):
    members = read_members(good)
    old_db = tmp_path / "old.db"
    old_db.write_bytes(members["answeroncue.db"])
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url=sqlite:///{old_db.as_posix()}"]})()
    command.downgrade(cfg, "0010_adaptive")
    older = variant(
        good,
        tmp_path / "older.zip",
        replace={"answeroncue.db": old_db.read_bytes()},
        patch={"alembic_revision": "0010_adaptive"},
        rehash=True,
    )
    preview = upload(api, older)
    assert preview.status_code == 200, preview.text
    result = apply(api, preview.json()["token"])
    assert result.status_code == 200, result.text
    url = secret_settings.effective_database_url
    engine = create_engine(url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            head_revision()
        )
        import_all_models()
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []
    assert api.get("/api/subjects").json()[0]["name"] == "Historia"
    db.expire_all()
    assert db.query(Recording).filter(Recording.audio_deleted_at.is_not(None)).count() == 0


def _usage(operation_id: str, input_tokens: int) -> UsageEvent:
    return UsageEvent(
        operation_id=operation_id,
        task="assessment",
        role="assessment",
        provider="openai",
        model="fake",
        usage=Usage(input_tokens=input_tokens),
        status="ok",
        error_kind=None,
        started_at=datetime(2026, 10, 24, tzinfo=UTC),
        duration_ms=5,
    )


def test_restore_keeps_usage_records(api, db, secret_settings, history):
    """Real usage_event rows written after the backup survive the restore."""
    recorder = DurableUsageRecorder(secret_settings.effective_database_url, InMemoryUsageRecorder())
    recorder.record(_usage("before-backup", 100))
    backup = make_backup(api)
    db.execute(text("DELETE FROM usage_event"))
    db.commit()
    recorder.record(_usage("after-backup", 900))
    token = upload(api, backup_path(secret_settings, backup)).json()["token"]
    assert apply(api, token).status_code == 200
    db.expire_all()
    rows = db.execute(text("SELECT operation_id, input_tokens FROM usage_event")).all()
    assert [tuple(r) for r in rows] == [("after-backup", 900)]
    head = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini"))).get_heads()
    assert len(head) == 1
    current = db.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    assert current == head


def test_restore_without_a_usage_table_on_either_side(api, db, secret_settings, history):
    backup = make_backup(api)
    token = upload(api, backup_path(secret_settings, backup)).json()["token"]
    assert apply(api, token).status_code == 200


def test_unknown_token(api, db):
    assert apply(api, "f" * 32).status_code == 404
    assert apply(api, "not-a-token").status_code == 404


def test_upload_streams_to_disk(api, db, secret_settings, history, good, monkeypatch):
    """The upload is written to the staging folder in chunks, never read whole."""
    monkeypatch.setattr(archive, "CHUNK", 64)
    response = upload(api, good)
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    assert (secret_settings.data_dir / "restore" / token / "upload.zip").stat().st_size == (
        good.stat().st_size
    )


def test_second_backup_in_the_same_second_has_its_own_name(api, db, secret_settings, history):
    first = make_backup(api)
    second = make_backup(api)
    assert first["name"] != second["name"]
    assert backup_path(secret_settings, first).is_file()
    assert backup_path(secret_settings, second).is_file()


def _database_with(members: dict[str, bytes], tmp_path: Path, sql: str) -> dict[str, bytes]:
    target = tmp_path / "edited.db"
    target.write_bytes(members["answeroncue.db"])
    conn = sqlite3.connect(target)
    try:
        conn.execute(sql)
        conn.commit()
    finally:
        conn.close()
    return {"answeroncue.db": target.read_bytes()}


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE recording SET stored_path = '../../outside.wav'",
        "UPDATE recording SET stored_path = 'C:/Windows/win.ini'",
        "UPDATE recording SET stored_path = '/etc/passwd'",
        "UPDATE recording SET stored_path = 'documents/a.txt'",
        "UPDATE document_version SET stored_path = 'recordings/../../x.txt'",
        "UPDATE document_version SET stored_path = 'settings.env'",
    ],
)
def test_paths_outside_the_data_folder_are_refused(
    api, db, secret_settings, history, good, tmp_path, sql
):
    edited = variant(
        good,
        tmp_path / "paths.zip",
        replace=_database_with(read_members(good), tmp_path, sql),
        rehash=True,
    )
    response = upload(api, edited)
    assert response.status_code == 422
    assert response.json()["detail"] == archive.UNEXPECTED_FILE


def test_nothing_outside_the_data_folder_is_deleted(client, db, secret_settings, history):
    outside = secret_settings.data_dir.parent / "outside.wav"
    outside.write_bytes(b"precious")
    db.execute(text("UPDATE recording SET stored_path = '../outside.wav'"))
    db.commit()
    subject_id = history["subject"].id
    assert client.get(f"/api/subjects/{subject_id}/deletion-preview").json()["recordings"] == 2
    some = history["recordings"][0].id
    assert client.get(f"/api/recordings/{some}/audio").status_code == 404
    assert client.delete(f"/api/recordings/{some}").status_code == 204
    done = client.delete(f"/api/subjects/{subject_id}", params={"confirm": "true"})
    assert done.status_code == 200
    assert outside.read_bytes() == b"precious"


def test_names_are_claimed_exclusively(tmp_path):
    """Two requests in the same second (two tabs) never get the same file."""
    from concurrent.futures import ThreadPoolExecutor

    from app.features.backup import storage

    now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
    with ThreadPoolExecutor(8) as pool:
        names = list(
            pool.map(lambda _: storage.free_name(tmp_path, "answeroncue-backup", now)[0], range(8))
        )
    assert len(set(names)) == 8


# --- space, races and left-over staging ---------------------------------------------------


def _unchanged(api, history, settings: Settings):
    """What a refused restore must leave exactly as it was (the backups folder included)."""
    return snapshot(api, history), tree(settings.data_dir)


def test_restore_rechecks_free_space_after_the_pre_restore_backup(
    api, db, secret_settings, history, good, monkeypatch
):
    token = upload(api, good).json()["token"]
    before = _unchanged(api, history, secret_settings)
    monkeypatch.setattr(archive, "_free_bytes", lambda path: 0)
    response = apply(api, token)
    monkeypatch.undo()
    assert response.status_code == 422 and response.json()["detail"] == archive.NO_SPACE
    assert _unchanged(api, history, secret_settings) == before  # pre-restore zip removed
    assert (secret_settings.data_dir / "restore" / token / "upload.zip").is_file()
    assert not (secret_settings.data_dir / "restore" / token / "new").exists()


def test_a_full_disk_while_extracting_is_no_space(
    api, db, secret_settings, history, good, monkeypatch
):
    token = upload(api, good).json()["token"]
    before = _unchanged(api, history, secret_settings)
    real_open = Path.open

    class FullDisk:
        def __init__(self, handle):
            self.handle = handle

        def write(self, data):
            raise OSError(errno.ENOSPC, "No space left on device")

        def close(self):
            self.handle.close()

    def opening(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        if "w" in mode and "new" in Path(path).parts:  # extracting into restore/<token>/new
            return FullDisk(handle)
        return handle

    monkeypatch.setattr(Path, "open", opening)
    response = apply(api, token)
    monkeypatch.undo()
    assert response.status_code == 422 and response.json()["detail"] == archive.NO_SPACE
    assert _unchanged(api, history, secret_settings) == before  # pre-restore zip removed


def test_a_restore_already_running_answers_busy(api, db, secret_settings, history, good):
    token = upload(api, good).json()["token"]
    before = _unchanged(api, history, secret_settings)
    assert archive._restore_lock.acquire(blocking=False)
    try:
        response = apply(api, token)
    finally:
        archive._restore_lock.release()
    assert response.status_code == 409 and response.json()["detail"] == archive.BUSY
    assert _unchanged(api, history, secret_settings) == before
    assert apply(api, token).status_code == 200


def test_a_round_started_during_the_pre_restore_backup_is_busy(
    api, db, secret_settings, history, good, monkeypatch
):
    token = upload(api, good).json()["token"]
    before = _unchanged(api, history, secret_settings)
    real_backup = archive.create_backup

    def backup_then_start_a_round(*args, **kwargs):
        made = real_backup(*args, **kwargs)
        with SessionLocal(secret_settings.effective_database_url) as other:
            make_round(other, history["subject"].id, at=datetime.now(UTC), status="active")
            other.commit()
        return made

    monkeypatch.setattr(archive, "create_backup", backup_then_start_a_round)
    response = apply(api, token)
    monkeypatch.undo()
    assert response.status_code == 409 and response.json()["detail"] == archive.BUSY
    after_snapshot, after_tree = _unchanged(api, history, secret_settings)
    assert after_tree == before[1]  # no file changed, the pre-restore zip is gone
    # only the round started meanwhile is new; the restore itself changed nothing
    assert after_snapshot["subjects"] == before[0]["subjects"]
    assert after_snapshot["round"] == before[0]["round"]
    assert not (secret_settings.data_dir / "restore" / token / "new").exists()


def _age(folder: Path, seconds: float) -> None:
    then = datetime.now().timestamp() - seconds
    for path in (*folder.rglob("*"), folder):
        os.utime(path, (then, then))


def test_upload_removes_staging_folders_older_than_a_day(api, db, secret_settings, history, good):
    root = secret_settings.data_dir / "restore"
    old = root / ("a" * 32)
    recent = root / ("b" * 32)
    for folder in (old, recent):
        folder.mkdir(parents=True)
        (folder / "upload.zip").write_bytes(b"left over")
    _age(old, 25 * 60 * 60)
    _age(recent, 23 * 60 * 60)
    stranger = root / "not-a-token"
    stranger.mkdir()
    _age(stranger, 30 * 60 * 60)

    response = upload(api, good)
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    assert not old.exists()
    assert recent.is_dir() and stranger.is_dir()
    assert (root / token / "upload.zip").is_file()


def test_staging_clean_up_never_fails_the_upload_nor_touches_a_running_restore(
    api, db, secret_settings, history, good, monkeypatch
):
    root = secret_settings.data_dir / "restore"
    old = root / ("c" * 32)
    old.mkdir(parents=True)
    (old / "upload.zip").write_bytes(b"left over")
    _age(old, 48 * 60 * 60)
    assert archive._restore_lock.acquire(blocking=False)  # a restore is being applied
    try:
        assert upload(api, good).status_code == 200
    finally:
        archive._restore_lock.release()
    assert old.is_dir()

    def broken(*args, **kwargs):
        raise OSError("locked by another program")

    monkeypatch.setattr(archive.shutil, "rmtree", broken)
    assert upload(api, good).status_code == 200
    monkeypatch.undo()
    assert old.is_dir()
