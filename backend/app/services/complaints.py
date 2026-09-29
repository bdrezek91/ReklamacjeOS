from collections.abc import Iterable, Mapping
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..models import (
    Attachment,
    Complaint,
    ComplaintEvent,
    ComplaintField,
    ComplaintMonthlyNumberCounter,
    ComplaintStatus,
    EmailOutbox,
    WhatsAppMessage,
    WhatsAppOutbox,
)
from .grouping import DraftOperationError

CARD_FIELDS = (
    "material_product",
    "quantity",
    "defect_description",
    "document_number",
    "customer_project",
    "notes",
)

WARSAW = ZoneInfo("Europe/Warsaw")
WHATSAPP_RESOLUTIONS = {
    "use_first_grade_next_pavilions": "Wykorzystać na 1 gatunek w następnych pawilonach.",
    "return_to_manufacturer": "Zwracamy do producenta.",
}


def _editable_complaint(db: Session, complaint_id: int) -> Complaint:
    complaint = db.get(Complaint, complaint_id)
    if complaint is None:
        raise DraftOperationError("Reklamacja nie istnieje")
    if complaint.merged_into_id is not None:
        raise DraftOperationError("Scalona reklamacja nie może być edytowana")
    if complaint.status not in {
        ComplaintStatus.DRAFT,
        ComplaintStatus.PENDING_APPROVAL,
        ComplaintStatus.ACCEPTED,
        ComplaintStatus.READY_TO_SEND,
    }:
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
    if (
        complaint.status == ComplaintStatus.READY_TO_SEND
        and db.scalar(select(EmailOutbox).where(EmailOutbox.complaint_id == complaint.id)) is not None
    ):
        raise DraftOperationError("Nie można zmienić karty po zleceniu wysyłki")
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
    complaint = db.get(Complaint, complaint_id)
    if complaint is None or complaint.merged_into_id is not None:
        raise DraftOperationError("Reklamacja nie istnieje")
    allowed = {
        ComplaintStatus.DRAFT: {ComplaintStatus.PENDING_APPROVAL},
        ComplaintStatus.PENDING_APPROVAL: {ComplaintStatus.DRAFT},
        ComplaintStatus.SENT: {ComplaintStatus.CLOSED},
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


def allocate_monthly_number(db: Session, *, year: int, month: int) -> str:
    if db.get_bind().dialect.name == "postgresql":
        lock_key = 732_000_000 + year * 100 + month
        db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})

    counter = db.get(ComplaintMonthlyNumberCounter, (year, month))
    if counter is None:
        sequence = 1
        db.add(ComplaintMonthlyNumberCounter(year=year, month=month, next_value=2))
    else:
        sequence = counter.next_value
        counter.next_value += 1
    db.flush()
    return f"R/{sequence:02d}/{month:02d}/{year}"


def accept_complaint(
    db: Session,
    complaint_id: int,
    *,
    actor: str,
    group_id: str,
    accepted_at: datetime | None = None,
) -> Complaint:
    complaint = db.get(Complaint, complaint_id)
    if complaint is None:
        raise DraftOperationError("Reklamacja nie istnieje")
    if complaint.merged_into_id is not None:
        raise DraftOperationError("Scalona reklamacja nie może być zaakceptowana")
    if complaint.status != ComplaintStatus.PENDING_APPROVAL:
        raise DraftOperationError("Do akceptacji można przekazać tylko reklamację w statusie „Do akceptacji”")
    if complaint.official_number is not None:
        raise DraftOperationError("Reklamacja ma już numer")
    if not group_id:
        raise DraftOperationError("Grupa WhatsApp nie jest skonfigurowana")

    local_time = accepted_at or datetime.now(WARSAW)
    official_number = allocate_monthly_number(db, year=local_time.year, month=local_time.month)
    body = (
        f"Przyjęto reklamację nr {official_number}. Proszę opisać reklamowane płyty: reklamacja nr {official_number}."
    )

    complaint.official_number = official_number
    complaint.status = ComplaintStatus.ACCEPTED
    complaint.updated_at = func.now()
    db.add(
        ComplaintEvent(
            complaint_id=complaint.id,
            event_type="complaint_accepted",
            actor=actor,
            details={"official_number": official_number},
        )
    )
    db.add(
        WhatsAppOutbox(
            complaint_id=complaint.id,
            group_id=group_id,
            body=body,
            message_kind="acceptance",
            status="pending",
        )
    )
    db.flush()
    return complaint


def enqueue_whatsapp_resolution(
    db: Session,
    complaint_id: int,
    resolution: str,
    *,
    actor: str,
    group_id: str,
) -> WhatsAppOutbox:
    complaint = db.get(Complaint, complaint_id)
    if complaint is None or complaint.merged_into_id is not None:
        raise DraftOperationError("Reklamacja nie istnieje")
    if complaint.status not in {
        ComplaintStatus.ACCEPTED,
        ComplaintStatus.READY_TO_SEND,
        ComplaintStatus.SENT,
    }:
        raise DraftOperationError("Decyzję można wysłać tylko dla zaakceptowanej reklamacji")
    if not complaint.official_number:
        raise DraftOperationError("Reklamacja nie ma numeru")
    if not group_id:
        raise DraftOperationError("Grupa WhatsApp nie jest skonfigurowana")
    body = WHATSAPP_RESOLUTIONS.get(resolution)
    if body is None:
        raise DraftOperationError("Nieznany status WhatsApp")
    existing = db.scalar(
        select(WhatsAppOutbox).where(
            WhatsAppOutbox.complaint_id == complaint.id,
            WhatsAppOutbox.message_kind == "resolution",
        )
    )
    if existing is not None:
        raise DraftOperationError("Decyzja WhatsApp dla tej reklamacji została już zlecona")

    complaint.whatsapp_resolution = resolution
    complaint.updated_at = func.now()
    item = WhatsAppOutbox(
        complaint_id=complaint.id,
        group_id=group_id,
        body=f"Reklamacja nr {complaint.official_number}: {body}",
        message_kind="resolution",
        status="pending",
    )
    db.add(item)
    db.add(
        ComplaintEvent(
            complaint_id=complaint.id,
            event_type="whatsapp_resolution_queued",
            actor=actor,
            details={"resolution": resolution},
        )
    )
    db.flush()
    return item


def retry_whatsapp_notification(
    db: Session,
    complaint_id: int,
    *,
    actor: str,
    message_kind: str = "acceptance",
) -> WhatsAppOutbox:
    item = db.scalar(
        select(WhatsAppOutbox).where(
            WhatsAppOutbox.complaint_id == complaint_id,
            WhatsAppOutbox.message_kind == message_kind,
        )
    )
    if item is None:
        raise DraftOperationError("Brak komunikatu WhatsApp dla tej reklamacji")
    if item.status != "failed":
        raise DraftOperationError("Ponowić można tylko nieudaną wysyłkę")

    item.status = "pending"
    item.last_error = None
    item.claimed_at = None
    db.add(
        ComplaintEvent(
            complaint_id=complaint_id,
            event_type=(
                "whatsapp_resolution_retry_requested"
                if message_kind == "resolution"
                else "whatsapp_number_retry_requested"
            ),
            actor=actor,
            details={"outbox_id": item.id},
        )
    )
    return item
