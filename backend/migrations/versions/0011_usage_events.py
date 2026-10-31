"""usage events

Revision ID: 0011_usage_events
Revises: 0010_adaptive
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_usage_events"
down_revision = "0010_adaptive"
branch_labels = None
depends_on = None

_INDEXED = ("operation_id", "started_at", "subject_id", "round_id")


def upgrade() -> None:
    # No foreign keys: the rows outlive the subject, round and attempt they describe.
    op.create_table(
        "usage_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.String(length=160), nullable=False),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("task", sa.String(length=80), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("error_kind", sa.String(length=40), nullable=True),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("audio_seconds", sa.Float(), server_default="0", nullable=False),
        sa.Column("characters", sa.Integer(), server_default="0", nullable=False),
        sa.Column("request_id", sa.String(length=120), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=True),
        sa.Column("round_id", sa.Integer(), nullable=True),
        sa.Column("attempt_id", sa.Integer(), nullable=True),
        sa.Column("follow_up_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in _INDEXED:
        op.create_index(f"ix_usage_event_{column}", "usage_event", [column])


def downgrade() -> None:
    for column in _INDEXED:
        op.drop_index(f"ix_usage_event_{column}", table_name="usage_event")
    op.drop_table("usage_event")
