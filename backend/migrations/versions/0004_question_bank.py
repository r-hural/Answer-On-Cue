"""question bank

Revision ID: 0004_question_bank
Revises: 0003_recording
Create Date: 2026-10-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_question_bank"
down_revision = "0003_recording"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "ai_job",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("document_version_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("steps_total", sa.Integer(), nullable=False),
        sa.Column("steps_done", sa.Integer(), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False),
        sa.Column("state_json", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["subject_id"], ["subject.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["document_version_id"], ["document_version.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_job_subject_id", "ai_job", ["subject_id"])
    op.create_table(
        "topic",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["subject_id"], ["subject.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["topic.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_topic_subject_id", "topic", ["subject_id"])
    op.create_table(
        "learning_objective",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["topic.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_learning_objective_topic_id", "learning_objective", ["topic_id"])
    op.create_table(
        "term",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column("term", sa.String(length=200), nullable=False),
        sa.Column("term_key", sa.String(length=200), nullable=False),
        sa.Column("definition", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["subject_id"], ["subject.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["topic.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject_id", "term_key"),
    )
    op.create_index("ix_term_subject_id", "term", ["subject_id"])
    op.create_table(
        "topic_passage",
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("passage_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["topic.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["passage_id"], ["source_passage.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("topic_id", "passage_id"),
    )
    op.create_table(
        "question_family",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("primary_topic_id", sa.Integer(), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(["subject_id"], ["subject.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["primary_topic_id"], ["topic.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_question_family_subject_id", "question_family", ["subject_id"])
    op.create_index("ix_question_family_primary_topic_id", "question_family", ["primary_topic_id"])
    op.create_table(
        "question_version",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("family_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("style", sa.String(length=12), nullable=False),
        sa.Column("difficulty", sa.String(length=12), nullable=False),
        sa.Column("source_policy", sa.String(length=10), nullable=False),
        sa.Column("objective_id", sa.Integer(), nullable=True),
        sa.Column("secondary_topic_ids", sa.Text(), nullable=False),
        sa.Column("suggested_prep_s", sa.Integer(), nullable=False),
        sa.Column("suggested_answer_s", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("provenance_json", sa.Text(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["family_id"], ["question_family.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["objective_id"], ["learning_objective.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("family_id", "version"),
    )
    op.create_index("ix_question_version_family_id", "question_version", ["family_id"])
    op.create_table(
        "rubric_version",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("question_version_id", sa.Integer(), nullable=False),
        sa.Column("rubric_json", sa.Text(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["question_version_id"], ["question_version.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("question_version_id"),
    )


def downgrade() -> None:
    op.drop_table("rubric_version")
    op.drop_index("ix_question_version_family_id", table_name="question_version")
    op.drop_table("question_version")
    op.drop_index("ix_question_family_primary_topic_id", table_name="question_family")
    op.drop_index("ix_question_family_subject_id", table_name="question_family")
    op.drop_table("question_family")
    op.drop_table("topic_passage")
    op.drop_index("ix_term_subject_id", table_name="term")
    op.drop_table("term")
    op.drop_index("ix_learning_objective_topic_id", table_name="learning_objective")
    op.drop_table("learning_objective")
    op.drop_index("ix_topic_subject_id", table_name="topic")
    op.drop_table("topic")
    op.drop_index("ix_ai_job_subject_id", table_name="ai_job")
    op.drop_table("ai_job")
