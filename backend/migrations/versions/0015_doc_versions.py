"""document versions: passage predecessor, objective document, carried-over versions

Revision ID: 0015_doc_versions
Revises: 0014_usage_costs
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_doc_versions"
down_revision = "0014_usage_costs"
branch_labels = None
depends_on = None


def _backfill_objectives(conn) -> None:
    """An objective's document is the single document of its topic's non-excluded passages."""
    conn.execute(
        sa.text(
            "UPDATE learning_objective SET document_id = ("
            "SELECT MIN(d.id) FROM topic_passage tp"
            " JOIN source_passage sp ON sp.id = tp.passage_id AND sp.excluded = 0"
            " JOIN document_version dv ON dv.id = sp.document_version_id"
            " JOIN document d ON d.id = dv.document_id"
            " WHERE tp.topic_id = learning_objective.topic_id"
            " HAVING COUNT(DISTINCT d.id) = 1)"
        )
    )


def upgrade() -> None:
    # No foreign keys: a predecessor and an objective's document are plain references.
    with op.batch_alter_table("source_passage", recreate="never") as batch:
        batch.add_column(sa.Column("previous_id", sa.Integer(), nullable=True))
    op.create_index("ix_source_passage_previous_id", "source_passage", ["previous_id"])
    with op.batch_alter_table("learning_objective", recreate="never") as batch:
        batch.add_column(sa.Column("document_id", sa.Integer(), nullable=True))
    op.create_index("ix_learning_objective_document_id", "learning_objective", ["document_id"])
    _backfill_objectives(op.get_bind())
    # Versions stored before this migration were not carried over (their passages have no
    # predecessors), so the server default 0 marks every existing row as such.
    with op.batch_alter_table("document_version", recreate="never") as batch:
        batch.add_column(
            sa.Column("carried_over", sa.Boolean(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("document_version", recreate="never") as batch:
        batch.drop_column("carried_over")
    op.drop_index("ix_learning_objective_document_id", table_name="learning_objective")
    with op.batch_alter_table("learning_objective", recreate="never") as batch:
        batch.drop_column("document_id")
    op.drop_index("ix_source_passage_previous_id", table_name="source_passage")
    with op.batch_alter_table("source_passage", recreate="never") as batch:
        batch.drop_column("previous_id")
