import logging
import sqlite3

from app.config import Settings
from app.db.legacy import migrate_legacy_db


def _make_db(path, value):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (v TEXT)")
    con.execute("INSERT INTO t VALUES (?)", (value,))
    con.commit()
    con.close()


def _value(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT v FROM t").fetchone()[0]
    finally:
        con.close()


def test_only_old_file_is_renamed_and_keeps_its_rows(tmp_path):
    _make_db(tmp_path / "proba-glosu.db", "old rows")
    assert migrate_legacy_db(tmp_path) == tmp_path / "answeroncue.db"
    assert not (tmp_path / "proba-glosu.db").exists()
    assert _value(tmp_path / "answeroncue.db") == "old rows"


def test_both_files_keep_new_and_leave_old_untouched(tmp_path, caplog):
    _make_db(tmp_path / "proba-glosu.db", "old")
    _make_db(tmp_path / "answeroncue.db", "new")
    with caplog.at_level(logging.WARNING):
        assert migrate_legacy_db(tmp_path) is None
    assert _value(tmp_path / "answeroncue.db") == "new"
    assert _value(tmp_path / "proba-glosu.db") == "old"
    assert "proba-glosu.db" in caplog.text


def test_neither_file_does_nothing(tmp_path):
    assert migrate_legacy_db(tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_first_start_with_default_path_renames_old_database(tmp_path):
    _make_db(tmp_path / "proba-glosu.db", "existing user data")
    url = Settings(data_dir=tmp_path).effective_database_url
    assert url == f"sqlite:///{(tmp_path / 'answeroncue.db').as_posix()}"
    assert _value(tmp_path / "answeroncue.db") == "existing user data"


def test_custom_database_url_does_not_rename(tmp_path):
    _make_db(tmp_path / "proba-glosu.db", "old")
    custom = f"sqlite:///{(tmp_path / 'other.db').as_posix()}"
    assert Settings(data_dir=tmp_path, database_url=custom).effective_database_url == custom
    assert (tmp_path / "proba-glosu.db").exists()
    assert not (tmp_path / "answeroncue.db").exists()
