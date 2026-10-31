"""usage costs

Revision ID: 0014_usage_costs
Revises: 0013_chapters
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_usage_costs"
down_revision = "0013_chapters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The pre-call estimate and the cost from provider-reported usage; NULL on older rows.
    with op.batch_alter_table("usage_event", recreate="never") as batch:
        batch.add_column(sa.Column("estimated_usd", sa.Float(), nullable=True))
        batch.add_column(sa.Column("cost_usd", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("usage_event", recreate="never") as batch:
        batch.drop_column("cost_usd")
        batch.drop_column("estimated_usd")
