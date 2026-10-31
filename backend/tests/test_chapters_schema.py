import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, select, text
from test_chapters_support import (
    BASE_REVISION,
    chapter_of,
    chapters_migration,
    chapters_of,
    migration_config,
    raw_document,
    raw_subject,
    raw_topic,
)
from test_topics_support import make_subject, make_topic, make_version, upgrade

from app.db.session import SessionLocal
from app.features.library.models import Chapter, Subject


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def test_migration_creates_table_and_columns(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/m.db"
    command.upgrade(migration_config(url), "head")
    inspector = inspect(create_engine(url))
    columns = {c["name"] for c in inspector.get_columns("chapter")}
    assert columns == {"id", "subject_id", "name", "is_default", "ordinal", "created_at",
                       "updated_at"}  # fmt: skip
    assert "chapter_id" in {c["name"] for c in inspector.get_columns("document")}
    assert "chapter_id" in {c["name"] for c in inspector.get_columns("topic")}
    assert "ix_chapter_subject_id" in {i["name"] for i in inspector.get_indexes("chapter")}
    assert "ix_document_chapter_id" in {i["name"] for i in inspector.get_indexes("document")}
    assert "ix_topic_chapter_id" in {i["name"] for i in inspector.get_indexes("topic")}
    assert inspector.get_foreign_keys("chapter") == []
    for table in ("document", "topic"):
        named = [fk for fk in inspector.get_foreign_keys(table)]
        assert all("chapter_id" not in fk["constrained_columns"] for fk in named)


def _old_world(conn) -> dict:
    s1 = raw_subject(conn, "Historia")
    s2 = raw_subject(conn, "Biologia")
    a, a_passages = raw_document(conn, s1, "Grecja")
    c, c_passages = raw_document(conn, s2, "Komórka")
    d, d_passages = raw_document(conn, s2, "Genetyka")
    return {
        "subjects": (s1, s2),
        "documents": (a, c, d),
        "ta": raw_topic(conn, s1, "Polis", a_passages),
        "tc": raw_topic(conn, s2, "Błona", c_passages),
        "td": raw_topic(conn, s2, "DNA", d_passages),
        "tcd": raw_topic(conn, s2, "Białka", [c_passages[0], d_passages[0]]),
        "tnone": raw_topic(conn, s2, "Bez fragmentów"),
        "tremoved": raw_topic(conn, s2, "Usunięty", d_passages[:1], status="removed"),
    }


def test_backfill_puts_everything_in_one_default_chapter(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/m.db"
    cfg = migration_config(url)
    command.upgrade(cfg, BASE_REVISION)
    engine = create_engine(url)
    with engine.begin() as conn:
        world = _old_world(conn)
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        defaults = {}
        for subject_id in world["subjects"]:
            rows = chapters_of(conn, subject_id)
            assert len(rows) == 1
            chapter_id, name, is_default, ordinal = rows[0]
            assert (name, bool(is_default), ordinal) == (None, True, 0)
            defaults[subject_id] = chapter_id
        s1, s2 = world["subjects"]
        a, c, d = world["documents"]
        assert chapter_of(conn, "document", a) == defaults[s1]
        assert {chapter_of(conn, "document", i) for i in (c, d)} == {defaults[s2]}
        assert chapter_of(conn, "topic", world["ta"]) == defaults[s1]
        for key in ("tc", "td", "tcd", "tnone", "tremoved"):
            assert chapter_of(conn, "topic", world[key]) == defaults[s2]

        # Decision 5, literally: a document already in a second chapter takes its own topics.
        second = conn.execute(
            text(
                "INSERT INTO chapter (subject_id, name, is_default, ordinal)"
                " VALUES (:s, 'Genetyka', 0, 1) RETURNING id"
            ),
            {"s": s2},
        ).scalar_one()
        conn.execute(
            text("UPDATE document SET chapter_id = :c WHERE id = :d"), {"c": second, "d": d}
        )
        conn.execute(text("UPDATE topic SET chapter_id = NULL"))
        chapters_migration()._backfill_topics(conn)
        assert chapter_of(conn, "topic", world["td"]) == second
        assert chapter_of(conn, "topic", world["tremoved"]) == second
        assert chapter_of(conn, "topic", world["tc"]) == defaults[s2]
        assert chapter_of(conn, "topic", world["tcd"]) == defaults[s2]
        assert chapter_of(conn, "topic", world["tnone"]) == defaults[s2]
        assert chapter_of(conn, "topic", world["ta"]) == defaults[s1]


def test_downgrade_keeps_rows(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/m.db"
    cfg = migration_config(url)
    command.upgrade(cfg, BASE_REVISION)
    engine = create_engine(url)
    with engine.begin() as conn:
        world = _old_world(conn)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, BASE_REVISION)
    inspector = inspect(create_engine(url))
    assert "chapter" not in inspector.get_table_names()
    assert "chapter_id" not in {c["name"] for c in inspector.get_columns("document")}
    assert "chapter_id" not in {c["name"] for c in inspector.get_columns("topic")}
    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM subject")).scalar() == 2
        assert conn.execute(text("SELECT COUNT(*) FROM document")).scalar() == 3
        assert conn.execute(text("SELECT COUNT(*) FROM topic")).scalar() == 6
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM chapter")).scalar() == 2
        for table in ("document", "topic"):
            nulls = conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE chapter_id IS NULL"))
            assert nulls.scalar() == 0
        assert chapter_of(conn, "topic", world["td"]) is not None


def test_orm_subject_gets_default_chapter(db, client):
    db.add(Subject(name="Historia"))
    db.commit()
    subject = db.scalars(select(Subject)).one()
    chapters = db.scalars(select(Chapter).where(Chapter.subject_id == subject.id)).all()
    assert [(c.name, c.is_default, c.ordinal) for c in chapters] == [(None, True, 0)]

    created = client.post("/api/subjects", json={"name": "Biologia", "language": "pl"}).json()
    listed = client.get(f"/api/subjects/{created['id']}/chapters").json()
    assert len(listed) == 1
    assert listed[0]["name"] is None and listed[0]["is_default"] is True


def test_document_and_topic_without_chapter_get_the_default(db):
    subject = make_subject(db)
    version, passages = make_version(db, subject.id, count=2, excluded=())
    topic = make_topic(db, subject.id, "Polis", passages)
    default = db.scalars(select(Chapter.id).where(Chapter.subject_id == subject.id)).one()
    assert version.document.chapter_id == default
    assert topic.chapter_id == default


def _backup_with_books(client, db, settings):
    from test_backup_archive import backup_path, make_backup

    subject = make_subject(db, "Historia PL")
    _, passages = make_version(db, subject.id, count=4, excluded=())
    make_topic(db, subject.id, "Polis", passages[:2])
    make_topic(db, subject.id, "Bez fragmentów")
    return backup_path(settings, make_backup(client))


def test_older_backup_gets_chapters(client, db, settings, tmp_path):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from test_backup_archive import apply, head_revision, read_members, upload, variant

    from app.db.base import Base
    from app.db.registry import import_all_models

    backup = _backup_with_books(client, db, settings)
    old_db = tmp_path / "old.db"
    old_db.write_bytes(read_members(backup)["answeroncue.db"])
    command.downgrade(migration_config(f"sqlite:///{old_db.as_posix()}"), BASE_REVISION)
    older = variant(
        backup,
        tmp_path / "older.zip",
        replace={"answeroncue.db": old_db.read_bytes()},
        patch={"alembic_revision": BASE_REVISION},
        rehash=True,
    )
    db.close()
    preview = upload(client, older)
    assert preview.status_code == 200, preview.text
    result = apply(client, preview.json()["token"])
    assert result.status_code == 200, result.text
    engine = create_engine(settings.effective_database_url)
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == head_revision()
        subject_ids = [r[0] for r in conn.execute(text("SELECT id FROM subject"))]
        assert subject_ids
        for subject_id in subject_ids:
            rows = chapters_of(conn, subject_id)
            assert [(name, bool(default)) for _, name, default, _ in rows] == [(None, True)]
        for table in ("document", "topic"):
            nulls = conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE chapter_id IS NULL"))
            assert nulls.scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM topic")).scalar() == 2
        import_all_models()
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []
    listed = client.get(f"/api/subjects/{subject_ids[0]}/chapters").json()
    assert len(listed) == 1 and listed[0]["topic_count"] == 2


def test_backup_counts_unchanged(client, db, settings):
    import json
    import zipfile

    backup = _backup_with_books(client, db, settings)
    with zipfile.ZipFile(backup) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert set(manifest["counts"]) == {
        "subjects", "documents", "document_versions", "topics", "questions", "rounds",
        "answers", "recordings",
    }  # fmt: skip
