"""Initial ReklamacjeOS schema.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    complaint_status = sa.Enum("DRAFT", "PENDING_APPROVAL", "SENT", "CLOSED", name="complaintstatus")

    op.create_table(
        "complaints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("draft_number", sa.String(32), nullable=False, unique=True),
        sa.Column("official_number", sa.String(32), unique=True),
        sa.Column("status", complaint_status, nullable=False),
        sa.Column("approved_data", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "whatsapp_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("complaint_id", sa.Integer(), sa.ForeignKey("complaints.id", ondelete="SET NULL")),
        sa.Column("wa_message_id", sa.String(255), nullable=False),
        sa.Column("group_id", sa.String(255), nullable=False),
        sa.Column("group_name", sa.String(255)),
        sa.Column("author_id", sa.String(255)),
        sa.Column("author_name", sa.String(255)),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("message_type", sa.String(64), nullable=False),
        sa.Column("quoted_message_id", sa.String(255)),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("source_payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("wa_message_id", name="uq_whatsapp_messages_wa_message_id"),
    )
    op.create_index("ix_whatsapp_messages_complaint_id", "whatsapp_messages", ["complaint_id"])
    op.create_index("ix_whatsapp_messages_group_id", "whatsapp_messages", ["group_id"])
    op.create_index("ix_whatsapp_messages_quoted_message_id", "whatsapp_messages", ["quoted_message_id"])

    op.create_table(
        "attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "whatsapp_message_id",
            sa.Integer(),
            sa.ForeignKey("whatsapp_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("storage_path", sa.String(1024), nullable=False, unique=True),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("include_in_email", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_attachments_whatsapp_message_id", "attachments", ["whatsapp_message_id"])
    op.create_index("ix_attachments_sha256", "attachments", ["sha256"])

    op.create_table(
        "complaint_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("complaint_id", sa.Integer(), sa.ForeignKey("complaints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_complaint_events_complaint_id", "complaint_events", ["complaint_id"])

    op.create_table(
        "complaint_fields",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("complaint_id", sa.Integer(), sa.ForeignKey("complaints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_name", sa.String(128), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Integer()),
        sa.Column("is_uncertain", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_complaint_fields_complaint_id", "complaint_fields", ["complaint_id"])

    op.create_table(
        "email_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("complaint_id", sa.Integer(), sa.ForeignKey("complaints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(998), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_email_drafts_complaint_id", "email_drafts", ["complaint_id"])

    op.create_table(
        "email_sent",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("complaint_id", sa.Integer(), sa.ForeignKey("complaints.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(998), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("attachments", sa.JSON(), nullable=False),
        sa.Column("message_id", sa.String(998)),
        sa.Column("smtp_result", sa.JSON(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_email_sent_complaint_id", "email_sent", ["complaint_id"])


def downgrade() -> None:
    op.drop_table("email_sent")
    op.drop_table("email_drafts")
    op.drop_table("complaint_fields")
    op.drop_table("complaint_events")
    op.drop_table("attachments")
    op.drop_table("whatsapp_messages")
    op.drop_table("complaints")
    sa.Enum(name="complaintstatus").drop(op.get_bind())
