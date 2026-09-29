"""Add WhatsApp complaint resolutions.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("complaints", sa.Column("whatsapp_resolution", sa.String(64), nullable=True))
    op.add_column(
        "whatsapp_outbox",
        sa.Column("message_kind", sa.String(32), nullable=False, server_default="acceptance"),
    )
    op.drop_constraint("whatsapp_outbox_complaint_id_key", "whatsapp_outbox", type_="unique")
    op.create_unique_constraint(
        "uq_whatsapp_outbox_complaint_kind",
        "whatsapp_outbox",
        ["complaint_id", "message_kind"],
    )
    op.alter_column("whatsapp_outbox", "message_kind", server_default=None)


def downgrade() -> None:
    op.drop_constraint("uq_whatsapp_outbox_complaint_kind", "whatsapp_outbox", type_="unique")
    op.execute("DELETE FROM whatsapp_outbox WHERE message_kind <> 'acceptance'")
    op.create_unique_constraint(
        "whatsapp_outbox_complaint_id_key",
        "whatsapp_outbox",
        ["complaint_id"],
    )
    op.drop_column("whatsapp_outbox", "message_kind")
    op.drop_column("complaints", "whatsapp_resolution")
