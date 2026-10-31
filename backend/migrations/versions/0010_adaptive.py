"""adaptive

Revision ID: 0010_adaptive
Revises: 0009_follow_ups
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_adaptive"
down_revision = "0009_follow_ups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No DB-level foreign key: rounds are deleted only through the subject cascade.
    with op.batch_alter_table("round", recreate="never") as batch:
        batch.add_column(sa.Column("parent_round_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("round_item", recreate="never") as batch:
        batch.add_column(sa.Column("selection", sa.String(length=12), nullable=True))
    op.create_index("uq_round_parent_type", "round", ["parent_round_id", "round_type"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_round_parent_type", table_name="round")
    # A plain ALTER (SQLite ≥ 3.35): rebuilding "round" with foreign keys on would cascade.
    with op.batch_alter_table("round_item", recreate="never") as batch:
        batch.drop_column("selection")
    with op.batch_alter_table("round", recreate="never") as batch:
        batch.drop_column("parent_round_id")
