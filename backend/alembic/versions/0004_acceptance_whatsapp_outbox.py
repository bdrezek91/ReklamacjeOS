"""Add acceptance status, monthly numbering and WhatsApp outbox.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE complaintstatus ADD VALUE IF NOT EXISTS 'ACCEPTED'")
    op.create_table(
        "complaint_monthly_number_counters",
        sa.Column("year", sa.Integer(), primary_key=True),
        sa.Column("month", sa.Integer(), primary_key=True),
        sa.Column("next_value", sa.Integer(), nullable=False),
    )
    op.create_table(
        "whatsapp_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "complaint_id",
            sa.Integer(),
            sa.ForeignKey("complaints.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("group_id", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("wa_message_id", sa.String(255), nullable=True, unique=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_whatsapp_outbox_complaint_id", "whatsapp_outbox", ["complaint_id"])
    op.create_index("ix_whatsapp_outbox_status", "whatsapp_outbox", ["status"])


def downgrade() -> None:
    op.drop_index("ix_whatsapp_outbox_status", table_name="whatsapp_outbox")
    op.drop_index("ix_whatsapp_outbox_complaint_id", table_name="whatsapp_outbox")
    op.drop_table("whatsapp_outbox")
    op.drop_table("complaint_monthly_number_counters")
    # PostgreSQL enum values are intentionally not removed during downgrade.
