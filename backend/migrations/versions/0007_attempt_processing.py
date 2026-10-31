"""attempt processing

Revision ID: 0007_attempt_processing
Revises: 0006_question_review
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_attempt_processing"
down_revision = "0006_question_review"
branch_labels = None
depends_on = None


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "attempt_processing",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("events_json", sa.Text(), nullable=True),
        sa.Column("transcript_text", sa.Text(), nullable=True),
        sa.Column("transcript_meta_json", sa.Text(), nullable=True),
        sa.Column("metrics_json", sa.Text(), nullable=True),
        sa.Column("assessment_json", sa.Text(), nullable=True),
        sa.Column("transcribe_tries", sa.Integer(), server_default="0", nullable=False),
        sa.Column("assess_tries", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_kind", sa.String(length=30), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        _now("created_at"),
        _now("updated_at"),
        sa.ForeignKeyConstraint(["attempt_id"], ["attempt.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id"),
    )


def downgrade() -> None:
    op.drop_table("attempt_processing")
