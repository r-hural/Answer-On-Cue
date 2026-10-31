"""The A/B tool refuses a live or in-use database, and upgrades only its own copy."""

import sqlite3

import pytest
from test_prompt_ab_support import (
    build_source,
    database_file,
    disk_show,
    fake_generators,
    needs_git,
    sha256,
    upgrade_to,
)
from test_topics_support import upgrade

from app.features.prompt_ab.run import (
    CountingGenerator,
    Refused,
    RunOptions,
    Spending,
    check_database,
    copy_database,
    run,
)


def plain_database(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(path).close()
    upgrade_to(path, "head")
    return path


def test_refuses_the_running_database(settings):
    upgrade(settings.effective_database_url)
    with pytest.raises(Refused, match="running app"):
        check_database(database_file(settings), settings)


def test_refuses_an_app_data_folder(settings, tmp_path):
    with_env = plain_database(tmp_path / "a" / "answeroncue.db")
    (with_env.parent / "settings.env").write_text("", encoding="utf-8")
    with pytest.raises(Refused, match="app data folder"):
        check_database(with_env, settings)
    with_recordings = plain_database(tmp_path / "b" / "answeroncue.db")
    (with_recordings.parent / "recordings").mkdir()
    with pytest.raises(Refused, match="app data folder"):
        check_database(with_recordings, settings)


def test_refuses_a_database_in_use(settings, tmp_path):
    path = plain_database(tmp_path / "copy" / "answeroncue.db")
    check_database(path, settings)  # a plain copy passes
    (path.parent / "answeroncue.db-wal").write_bytes(b"")
    with pytest.raises(Refused, match="in use"):
        check_database(path, settings)


def test_refuses_a_non_sqlite_file(settings, tmp_path):
    text = tmp_path / "notes.db"
    text.write_text("not a database", encoding="utf-8")
    with pytest.raises(Refused, match="Not a SQLite"):
        check_database(text, settings)
    with pytest.raises(Refused, match="No such"):
        check_database(tmp_path / "missing.db", settings)


def test_copy_is_upgraded_and_original_untouched(tmp_path):
    original = tmp_path / "old" / "answeroncue.db"
    original.parent.mkdir()
    sqlite3.connect(original).close()
    upgrade_to(original, "0015_doc_versions")
    before = (sha256(original), original.stat().st_mtime_ns)

    copy = copy_database(original, tmp_path / "run" / "copy.db")

    assert (sha256(original), original.stat().st_mtime_ns) == before
    conn = sqlite3.connect(copy)
    try:
        (head,) = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(subject)")}
    finally:
        conn.close()
    assert head == "0016_content_languages"
    assert "language" in columns
    assert sorted(p.name for p in original.parent.iterdir()) == ["answeroncue.db"]


@needs_git
def test_upgrade_targets_the_copy_only(client, settings, tmp_path):
    source = build_source(client, settings, tmp_path / "src" / "answeroncue.db", answers=1)
    running = database_file(settings)
    before = sha256(running)
    copy_database(source, tmp_path / "run" / "copy.db")
    assert sha256(running) == before
    factory = fake_generators()
    options = RunOptions(db=source, old_ref="HEAD", dry_run=True, out_dir=tmp_path / "out")
    run(settings, options, show=disk_show, generators=factory, allow_same_versions=True)
    assert sha256(running) == before
    assert factory.built == []


def test_copy_never_lands_on_the_source(tmp_path):
    db = plain_database(tmp_path / "source-copy.db")
    before = db.read_bytes()
    with pytest.raises(Refused):
        copy_database(db, db)
    with pytest.raises(Refused):
        copy_database(db, tmp_path / "other.db")
    assert db.read_bytes() == before
    assert not (tmp_path / "other.db").exists()


def test_counting_generator_refuses_past_the_plan():
    seen = []

    class Inner:
        def generate_structured(self, task, system, messages, schema, *, operation_id, **kwargs):
            seen.append(operation_id)
            raise AssertionError("stop after counting")

    counting = CountingGenerator(Inner(), Spending(limit=1), "generation")
    with pytest.raises(AssertionError):
        counting.generate_structured("t", "s", [], None, operation_id="one")
    with pytest.raises(Refused):
        counting.generate_structured("t", "s", [], None, operation_id="two")
    assert seen == ["one"]
