"""recording

Revision ID: 0003_recording
Revises: 0002_library
Create Date: 2026-10-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_recording"
down_revision = "0002_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recording",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("mime", sa.String(length=30), nullable=False),
        sa.Column("sample_rate", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("stored_path", sa.String(length=300), nullable=False),
        sa.Column("vad_json", sa.Text(), nullable=True),
        sa.Column("vad_engine", sa.String(length=40), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("recording")
