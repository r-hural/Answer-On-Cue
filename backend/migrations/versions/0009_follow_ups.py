"""follow-ups

Revision ID: 0009_follow_ups
Revises: 0008_transcript_report
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_follow_ups"
down_revision = "0008_transcript_report"
branch_labels = None
depends_on = None


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    with op.batch_alter_table("round", recreate="never") as batch:
        batch.add_column(
            sa.Column("part", sa.String(length=10), server_default="main", nullable=False)
        )
    op.create_table(
        "follow_up",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("round_id", sa.Integer(), nullable=False),
        sa.Column("main_item_id", sa.Integer(), nullable=False),
        sa.Column("main_attempt_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("target", sa.String(length=24), nullable=True),
        sa.Column("tries", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failure_kind", sa.String(length=30), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("provenance_json", sa.Text(), nullable=True),
        _now("created_at"),
        _now("updated_at"),
        sa.ForeignKeyConstraint(["round_id"], ["round.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["main_item_id"], ["round_item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["main_attempt_id"], ["attempt.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["round_item.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("main_item_id"),
    )
    op.create_index("ix_follow_up_round_id", "follow_up", ["round_id"])


def downgrade() -> None:
    op.drop_index("ix_follow_up_round_id", table_name="follow_up")
    op.drop_table("follow_up")
    # A plain ALTER (SQLite ≥ 3.35): rebuilding "round" with foreign keys on would cascade.
    with op.batch_alter_table("round", recreate="never") as batch:
        batch.drop_column("part")
