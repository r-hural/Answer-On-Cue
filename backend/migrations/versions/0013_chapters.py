"""chapters

Revision ID: 0013_chapters
Revises: 0012_audio_retention
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_chapters"
down_revision = "0012_audio_retention"
branch_labels = None
depends_on = None

_DEFAULT_OF_SUBJECT = (
    "(SELECT c.id FROM chapter c WHERE c.subject_id = {table}.subject_id AND c.is_default = 1)"
)


def _backfill_topics(conn) -> None:
    """A topic's chapter is the single chapter of its passages' documents, else the default."""
    conn.execute(
        sa.text(
            "UPDATE topic SET chapter_id = COALESCE("
            "(SELECT MIN(d.chapter_id) FROM topic_passage tp"
            " JOIN source_passage sp ON sp.id = tp.passage_id"
            " JOIN document_version dv ON dv.id = sp.document_version_id"
            " JOIN document d ON d.id = dv.document_id"
            " WHERE tp.topic_id = topic.id AND sp.excluded = 0"
            " HAVING COUNT(DISTINCT d.chapter_id) = 1), "
            + _DEFAULT_OF_SUBJECT.format(table="topic")
            + ")"
        )
    )


def upgrade() -> None:
    # No foreign keys: chapters are deleted with their subject by the app (no cascade needed).
    op.create_table(
        "chapter",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("is_default", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chapter_subject_id", "chapter", ["subject_id"])
    with op.batch_alter_table("document", recreate="never") as batch:
        batch.add_column(sa.Column("chapter_id", sa.Integer(), nullable=True))
    op.create_index("ix_document_chapter_id", "document", ["chapter_id"])
    with op.batch_alter_table("topic", recreate="never") as batch:
        batch.add_column(sa.Column("chapter_id", sa.Integer(), nullable=True))
    op.create_index("ix_topic_chapter_id", "topic", ["chapter_id"])

    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT INTO chapter (subject_id, name, is_default, ordinal, created_at, updated_at) "
            "SELECT id, NULL, 1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP FROM subject"
        )
    )
    conn.execute(
        sa.text("UPDATE document SET chapter_id = " + _DEFAULT_OF_SUBJECT.format(table="document"))
    )
    _backfill_topics(conn)


def downgrade() -> None:
    op.drop_index("ix_topic_chapter_id", table_name="topic")
    with op.batch_alter_table("topic", recreate="never") as batch:
        batch.drop_column("chapter_id")
    op.drop_index("ix_document_chapter_id", table_name="document")
    with op.batch_alter_table("document", recreate="never") as batch:
        batch.drop_column("chapter_id")
    op.drop_index("ix_chapter_subject_id", table_name="chapter")
    op.drop_table("chapter")
