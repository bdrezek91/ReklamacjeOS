"""Add business resolution strategy to complaints.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("complaints", sa.Column("resolution_strategy", sa.String(64), nullable=True))
    op.add_column("complaints", sa.Column("resolution_discount_percent", sa.Integer(), nullable=True))
    op.add_column("complaints", sa.Column("resolution_decided_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_complaints_resolution_strategy", "complaints", ["resolution_strategy"])
    op.execute("""
        UPDATE complaints
        SET resolution_strategy = CASE whatsapp_resolution
            WHEN 'use_first_grade_next_pavilions' THEN 'reuse_first_grade'
            WHEN 'return_to_manufacturer' THEN 'return_to_supplier'
            ELSE resolution_strategy
        END
        WHERE whatsapp_resolution IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_index("ix_complaints_resolution_strategy", table_name="complaints")
    op.drop_column("complaints", "resolution_decided_at")
    op.drop_column("complaints", "resolution_discount_percent")
    op.drop_column("complaints", "resolution_strategy")
