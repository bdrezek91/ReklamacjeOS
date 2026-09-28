"""Add non-destructive draft merge tracking.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("complaints", sa.Column("merged_into_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_complaints_merged_into_id_complaints",
        "complaints",
        "complaints",
        ["merged_into_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_complaints_merged_into_id", "complaints", ["merged_into_id"])


def downgrade() -> None:
    op.drop_index("ix_complaints_merged_into_id", table_name="complaints")
    op.drop_constraint("fk_complaints_merged_into_id_complaints", "complaints", type_="foreignkey")
    op.drop_column("complaints", "merged_into_id")
