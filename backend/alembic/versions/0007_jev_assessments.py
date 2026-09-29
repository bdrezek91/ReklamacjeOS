"""Add TypeSafe Jev assessments.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jev_assessments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "complaint_id",
            sa.Integer(),
            sa.ForeignKey("complaints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_jev_assessments_complaint_id", "jev_assessments", ["complaint_id"])
    op.create_index("ix_jev_assessments_input_hash", "jev_assessments", ["input_hash"])
    op.create_index("ix_jev_assessments_status", "jev_assessments", ["status"])


def downgrade() -> None:
    op.drop_index("ix_jev_assessments_status", table_name="jev_assessments")
    op.drop_index("ix_jev_assessments_input_hash", table_name="jev_assessments")
    op.drop_index("ix_jev_assessments_complaint_id", table_name="jev_assessments")
    op.drop_table("jev_assessments")
