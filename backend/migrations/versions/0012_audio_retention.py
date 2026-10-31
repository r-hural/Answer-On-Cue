"""audio retention

Revision ID: 0012_audio_retention
Revises: 0011_usage_events
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_audio_retention"
down_revision = "0011_usage_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No DB-level foreign key; the row (duration, VAD) stays when its audio file is deleted.
    with op.batch_alter_table("recording", recreate="never") as batch:
        batch.add_column(sa.Column("audio_deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("recording", recreate="never") as batch:
        batch.drop_column("audio_deleted_at")
