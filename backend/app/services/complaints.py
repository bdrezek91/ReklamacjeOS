from collections.abc import Iterable, Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Attachment,
    Complaint,
    ComplaintEvent,
    ComplaintField,
    ComplaintStatus,
    WhatsAppMessage,
)
from .grouping import DraftOperationError

CARD_FIELDS = (
    "supplier",
    "material_product",
    "quantity",
    "defect_description",
    "document_number",
    "customer_project",
    "notes",
)


def _editable_complaint(db: Session, complaint_id: int) -> Complaint:
    complaint = db.get(Complaint, complaint_id)
    if complaint is None:
        raise DraftOperationError("Reklamacja nie istnieje")
    if complaint.merged_into_id is not None:
        raise DraftOperationError("Scalona reklamacja nie może być edytowana")
    if complaint.status not in {ComplaintStatus.DRAFT, ComplaintStatus.PENDING_APPROVAL}:
        raise DraftOperationError("Reklamacja w tym statusie nie może być edytowana")
    return complaint


def update_complaint_card(
    db: Session,
    complaint_id: int,
    *,
    values: Mapping[str, str],
    included_attachment_ids: Iterable[int],
    actor: str,
) -> Complaint:
    complaint = _editable_complaint(db, complaint_id)
    current = dict(complaint.approved_data or {})
    changed_fields: list[str] = []

    for field_name in CARD_FIELDS:
        value = values.get(field_name, "").strip()
        if current.get(field_name, "") == value:
            continue
        current[field_name] = value
        changed_fields.append(field_name)
        db.add(
            ComplaintField(
                complaint_id=complaint.id,
                field_name=field_name,
                source="manual",
                value={"value": value},
                confidence=None,
                is_uncertain=False,
            )
        )

    included = set(included_attachment_ids)
    attachments = db.scalars(
        select(Attachment)
        .join(WhatsAppMessage, Attachment.whatsapp_message_id == WhatsAppMessage.id)
        .where(WhatsAppMessage.complaint_id == complaint.id)
    ).all()
    changed_attachments: list[int] = []
    for attachment in attachments:
        include = attachment.id in included
        if attachment.include_in_email != include:
            attachment.include_in_email = include
            changed_attachments.append(attachment.id)

    complaint.approved_data = current
    complaint.updated_at = func.now()
    if changed_fields or changed_attachments:
        db.add(
            ComplaintEvent(
                complaint_id=complaint.id,
                event_type="complaint_card_updated",
                actor=actor,
                details={
                    "changed_fields": changed_fields,
                    "changed_attachment_ids": changed_attachments,
                },
            )
        )
    return complaint


def change_complaint_status(
    db: Session,
    complaint_id: int,
    target_status: ComplaintStatus,
    *,
    actor: str,
) -> Complaint:
    complaint = _editable_complaint(db, complaint_id)
    allowed = {
        ComplaintStatus.DRAFT: {ComplaintStatus.PENDING_APPROVAL},
        ComplaintStatus.PENDING_APPROVAL: {ComplaintStatus.DRAFT},
    }
    if target_status not in allowed.get(complaint.status, set()):
        raise DraftOperationError("Niedozwolona zmiana statusu")

    previous_status = complaint.status
    complaint.status = target_status
    complaint.updated_at = func.now()
    db.add(
        ComplaintEvent(
            complaint_id=complaint.id,
            event_type="status_changed",
            actor=actor,
            details={"from": previous_status.value, "to": target_status.value},
        )
    )
    return complaint
