from alembic import command
from sqlalchemy import create_engine, inspect, text
from test_chapters_support import raw_document, raw_subject, raw_topic
from test_doc_versions_support import (
    BASE_REVISION,
    doc_versions_migration,
    migration_config,
    objective_document,
    raw_objective,
)
from test_topics_support import make_subject, make_topic, make_version

NEW_COLUMNS = {"source_passage": "previous_id", "learning_objective": "document_id"}
NEW_INDEXES = {
    "source_passage": "ix_source_passage_previous_id",
    "learning_objective": "ix_learning_objective_document_id",
}


def _old_world(conn) -> dict:
    subject = raw_subject(conn, "Historia")
    a, a_passages = raw_document(conn, subject, "Grecja", passages=3)
    b, b_passages = raw_document(conn, subject, "Rzym", passages=2)
    conn.execute(text("UPDATE source_passage SET excluded = 1 WHERE id = :p"), {"p": a_passages[2]})
    only_a = raw_topic(conn, subject, "Polis", a_passages[:2])
    a_and_b = raw_topic(conn, subject, "Porównanie", [a_passages[0], b_passages[0]])
    nothing = raw_topic(conn, subject, "Bez fragmentów")
    excluded = raw_topic(conn, subject, "Wykluczony", [a_passages[2]])
    return {
        "a": a,
        "objectives": [
            raw_objective(conn, only_a),
            raw_objective(conn, a_and_b),
            raw_objective(conn, nothing),
            raw_objective(conn, excluded),
        ],
    }


def test_migration_adds_columns(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/m.db"
    command.upgrade(migration_config(url), "head")
    inspector = inspect(create_engine(url))
    for table, column in NEW_COLUMNS.items():
        found = {c["name"]: c for c in inspector.get_columns(table)}
        assert column in found and found[column]["nullable"] is True
        assert NEW_INDEXES[table] in {i["name"] for i in inspector.get_indexes(table)}
        for fk in inspector.get_foreign_keys(table):
            assert column not in fk["constrained_columns"]


def test_backfill_objective_documents(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/m.db"
    cfg = migration_config(url)
    command.upgrade(cfg, BASE_REVISION)
    engine = create_engine(url)
    with engine.begin() as conn:
        world = _old_world(conn)
    command.upgrade(cfg, "head")
    expected = [world["a"], None, None, None]
    with engine.begin() as conn:
        assert [objective_document(conn, o) for o in world["objectives"]] == expected
        conn.execute(text("UPDATE learning_objective SET document_id = NULL"))
        doc_versions_migration()._backfill_objectives(conn)
        assert [objective_document(conn, o) for o in world["objectives"]] == expected


def test_downgrade_keeps_rows(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/m.db"
    cfg = migration_config(url)
    command.upgrade(cfg, BASE_REVISION)
    engine = create_engine(url)
    with engine.begin() as conn:
        _old_world(conn)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, BASE_REVISION)
    inspector = inspect(create_engine(url))
    for table, column in NEW_COLUMNS.items():
        assert column not in {c["name"] for c in inspector.get_columns(table)}
        assert NEW_INDEXES[table] not in {i["name"] for i in inspector.get_indexes(table)}
    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM source_passage")).scalar() == 5
        assert conn.execute(text("SELECT COUNT(*) FROM learning_objective")).scalar() == 4
    command.upgrade(cfg, "head")
    inspector = inspect(create_engine(url))
    for table, column in NEW_COLUMNS.items():
        assert column in {c["name"] for c in inspector.get_columns(table)}


def test_older_backup_restores_to_head(client, settings, tmp_path):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from test_backup_archive import (
        apply,
        backup_path,
        head_revision,
        make_backup,
        read_members,
        upload,
        variant,
    )
    from test_topics_support import upgrade

    from app.db.base import Base
    from app.db.registry import import_all_models
    from app.db.session import SessionLocal

    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as db:
        subject = make_subject(db, "Historia PL")
        _, passages = make_version(db, subject.id, count=4, excluded=())
        make_topic(db, subject.id, "Polis", passages[:2], objectives=("Wyjaśnić polis",))
    backup = backup_path(settings, make_backup(client))
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
    preview = upload(client, older)
    assert preview.status_code == 200, preview.text
    result = apply(client, preview.json()["token"])
    assert result.status_code == 200, result.text
    engine = create_engine(settings.effective_database_url)
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == head_revision()
        documents = conn.execute(text("SELECT document_id FROM learning_objective")).all()
        assert len(documents) == 1 and documents[0][0] is not None
        import_all_models()
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []
