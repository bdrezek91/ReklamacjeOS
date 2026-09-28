"""Add suppliers and manual correspondence workflow.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE complaintstatus ADD VALUE IF NOT EXISTS 'READY_TO_SEND'")
    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("contact_person", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_suppliers_active", "suppliers", ["active"])
    suppliers = sa.table(
        "suppliers",
        sa.column("name", sa.String()),
        sa.column("email", sa.String()),
        sa.column("active", sa.Boolean()),
    )
    op.bulk_insert(
        suppliers,
        [{"name": "Paneltech", "email": "reklamacje@paneltech.pl", "active": True}],
    )
    op.add_column("complaints", sa.Column("supplier_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_complaints_supplier_id_suppliers",
        "complaints",
        "suppliers",
        ["supplier_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_complaints_supplier_id", "complaints", ["supplier_id"])
    op.create_unique_constraint("uq_email_drafts_complaint_id", "email_drafts", ["complaint_id"])


def downgrade() -> None:
    op.drop_constraint("uq_email_drafts_complaint_id", "email_drafts", type_="unique")
    op.drop_index("ix_complaints_supplier_id", table_name="complaints")
    op.drop_constraint("fk_complaints_supplier_id_suppliers", "complaints", type_="foreignkey")
    op.drop_column("complaints", "supplier_id")
    op.drop_index("ix_suppliers_active", table_name="suppliers")
    op.drop_table("suppliers")
    # PostgreSQL enum values are intentionally not removed during downgrade.
