"""question review

Revision ID: 0006_question_review
Revises: 0005_rounds
Create Date: 2026-10-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_question_review"
down_revision = "0005_rounds"
branch_labels = None
depends_on = None


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "question_review",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("question_version_id", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(length=8), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        _now("created_at"),
        _now("updated_at"),
        sa.ForeignKeyConstraint(
            ["question_version_id"], ["question_version.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("question_version_id"),
    )


def downgrade() -> None:
    op.drop_table("question_review")
