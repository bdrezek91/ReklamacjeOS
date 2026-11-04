"""Separate, idempotent outbox for supplier agreement replies.

Revision ID: 0012
Revises: 0011
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "supplier_reply_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("complaint_id", sa.Integer(), sa.ForeignKey("complaints.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reply_to_source_id", sa.String(255), nullable=False),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("sender", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(998), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("in_reply_to", sa.String(998), nullable=False),
        sa.Column("references", sa.Text(), nullable=False),
        sa.Column("message_id", sa.String(998), nullable=False),
        sa.Column("discount_percent", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("complaint_id", "reply_to_source_id", name="uq_supplier_reply_outbox_reply"),
        sa.UniqueConstraint("message_id", name="uq_supplier_reply_outbox_message_id"),
    )
    op.create_index("ix_supplier_reply_outbox_status", "supplier_reply_outbox", ["status"])


def downgrade() -> None:
    op.drop_index("ix_supplier_reply_outbox_status", table_name="supplier_reply_outbox")
    op.drop_table("supplier_reply_outbox")
