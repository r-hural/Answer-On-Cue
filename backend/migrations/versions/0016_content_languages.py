"""content languages: subject language, content_language table

Revision ID: 0016_content_languages
Revises: 0015_doc_versions
Create Date: 2026-10-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_content_languages"
down_revision = "0015_doc_versions"
branch_labels = None
depends_on = None

# Literal codes in SUPPORTED order: a migration never imports app code.
CODES = ("pl", "en", "de", "fr", "es", "it", "uk")


def upgrade() -> None:
    # No foreign key; subjects created before content languages existed are Polish.
    with op.batch_alter_table("subject", recreate="never") as batch:
        batch.add_column(
            sa.Column("language", sa.String(length=8), nullable=False, server_default="pl")
        )
    table = op.create_table(
        "content_language",
        sa.Column("code", sa.String(length=8), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("voice_provider", sa.String(length=32), nullable=True),
        sa.Column("voice_name", sa.String(length=200), nullable=True),
        sa.PrimaryKeyConstraint("code"),
    )
    op.bulk_insert(
        table,
        [
            {"code": code, "enabled": code == "pl", "voice_provider": None, "voice_name": None}
            for code in CODES
        ],
    )


def downgrade() -> None:
    op.drop_table("content_language")
    with op.batch_alter_table("subject", recreate="never") as batch:
        batch.drop_column("language")
