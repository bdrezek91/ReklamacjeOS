"""Add local OCR results.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ocr_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "attachment_id",
            sa.Integer(),
            sa.ForeignKey("attachments.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("engine", sa.String(32), nullable=False),
        sa.Column("engine_version", sa.String(64), nullable=True),
        sa.Column("language", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("corrected_text", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ocr_results_attachment_id", "ocr_results", ["attachment_id"])
    op.create_index("ix_ocr_results_status", "ocr_results", ["status"])


def downgrade() -> None:
    op.drop_index("ix_ocr_results_status", table_name="ocr_results")
    op.drop_index("ix_ocr_results_attachment_id", table_name="ocr_results")
    op.drop_table("ocr_results")
