"""transcript report

Revision ID: 0008_transcript_report
Revises: 0007_attempt_processing
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_transcript_report"
down_revision = "0007_attempt_processing"
branch_labels = None
depends_on = None


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "transcript_report",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.Integer(), nullable=False),
        sa.Column("corrected_text", sa.Text(), nullable=False),
        _now("created_at"),
        _now("updated_at"),
        sa.ForeignKeyConstraint(["attempt_id"], ["attempt.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id"),
    )


def downgrade() -> None:
    op.drop_table("transcript_report")
