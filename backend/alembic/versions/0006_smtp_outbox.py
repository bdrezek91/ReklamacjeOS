"""Add durable SMTP outbox.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "complaint_id",
            sa.Integer(),
            sa.ForeignKey("complaints.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("sender", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(998), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("attachments", sa.JSON(), nullable=False),
        sa.Column("message_id", sa.String(998), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_email_outbox_complaint_id", "email_outbox", ["complaint_id"])
    op.create_index("ix_email_outbox_status", "email_outbox", ["status"])


def downgrade() -> None:
    op.drop_index("ix_email_outbox_status", table_name="email_outbox")
    op.drop_index("ix_email_outbox_complaint_id", table_name="email_outbox")
    op.drop_table("email_outbox")
