"""Close complaints with an existing WhatsApp resolution.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        WITH resolved AS (
            SELECT id, lower(status::text) AS previous_status
            FROM complaints
            WHERE whatsapp_resolution IS NOT NULL
              AND status::text IN ('ACCEPTED', 'READY_TO_SEND', 'SENT')
        ), changed AS (
            UPDATE complaints AS complaint
            SET status = 'CLOSED', updated_at = now()
            FROM resolved
            WHERE complaint.id = resolved.id
            RETURNING complaint.id, resolved.previous_status
        )
        INSERT INTO complaint_events (complaint_id, event_type, actor, details, created_at)
        SELECT
            id,
            'status_changed',
            'migration-0009',
            jsonb_build_object(
                'from', previous_status,
                'to', 'closed',
                'reason', 'whatsapp_resolution_backfill'
            ),
            now()
        FROM changed
        """
    )


def downgrade() -> None:
    # The previous workflow state cannot be reconstructed safely after later activity.
    pass
