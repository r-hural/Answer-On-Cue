"""rounds

Revision ID: 0005_rounds
Revises: 0004_question_bank
Create Date: 2026-10-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_rounds"
down_revision = "0004_question_bank"
branch_labels = None
depends_on = None


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _timestamps() -> list[sa.Column]:
    return [_now("created_at"), _now("updated_at")]


def upgrade() -> None:
    op.create_table(
        "round",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("round_type", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("phase", sa.String(length=12), nullable=False),
        sa.Column("paused_from_phase", sa.String(length=12), nullable=True),
        sa.Column("current_index", sa.Integer(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        _now("started_at"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["subject_id"], ["subject.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_round_subject_id", "round", ["subject_id"])
    op.create_table(
        "round_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("round_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("parent_item_id", sa.Integer(), nullable=True),
        sa.Column("question_version_id", sa.Integer(), nullable=False),
        sa.Column("rubric_version_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["round_id"], ["round.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_item_id"], ["round_item.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["question_version_id"], ["question_version.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["rubric_version_id"], ["rubric_version.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("round_id", "ordinal", "role"),
    )
    op.create_index("ix_round_item_round_id", "round_item", ["round_id"])
    op.create_table(
        "attempt",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("round_item_id", sa.Integer(), nullable=False),
        sa.Column("attempt_kind", sa.String(length=12), nullable=False),
        sa.Column("parent_attempt_id", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("processing_status", sa.String(length=12), nullable=True),
        sa.Column("start_reason", sa.String(length=10), nullable=True),
        sa.Column("recording_id", sa.Integer(), nullable=True),
        _now("started_at"),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["round_item_id"], ["round_item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_attempt_id"], ["attempt.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recording_id"], ["recording.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_attempt_round_item_id", "attempt", ["round_item_id"])
    op.create_table(
        "round_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("round_id", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("from_phase", sa.String(length=12), nullable=False),
        sa.Column("to_phase", sa.String(length=12), nullable=False),
        sa.Column("status_after", sa.String(length=12), nullable=False),
        _now("at"),
        sa.ForeignKeyConstraint(["round_id"], ["round.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attempt_id"], ["attempt.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_round_event_round_id", "round_event", ["round_id"])


def downgrade() -> None:
    op.drop_index("ix_round_event_round_id", table_name="round_event")
    op.drop_table("round_event")
    op.drop_index("ix_attempt_round_item_id", table_name="attempt")
    op.drop_table("attempt")
    op.drop_index("ix_round_item_round_id", table_name="round_item")
    op.drop_table("round_item")
    op.drop_index("ix_round_subject_id", table_name="round")
    op.drop_table("round")
