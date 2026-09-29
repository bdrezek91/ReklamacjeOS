from datetime import datetime
from email.utils import make_msgid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Attachment,
    Complaint,
    ComplaintEvent,
    ComplaintStatus,
    EmailDraft,
    EmailOutbox,
    EmailSent,
    Supplier,
    WhatsAppMessage,
)
from .grouping import DraftOperationError


def validate_email(value: str) -> str:
    email = value.strip()
    if not email or "@" not in email or any(character in email for character in "\r\n"):
        raise DraftOperationError("Podaj poprawny adres e-mail dostawcy")
    return email


def save_supplier(
    db: Session,
    *,
    supplier_id: int | None,
    name: str,
    email: str,
    contact_person: str,
    phone: str,
    notes: str,
    active: bool,
) -> Supplier:
    clean_name = name.strip()
    if not clean_name:
        raise DraftOperationError("Nazwa dostawcy jest wymagana")
    clean_email = validate_email(email)

    supplier = db.get(Supplier, supplier_id) if supplier_id is not None else Supplier()
    if supplier is None:
        raise DraftOperationError("Dostawca nie istnieje")
    duplicate = db.scalar(select(Supplier).where(Supplier.name == clean_name, Supplier.id != (supplier_id or 0)))
    if duplicate is not None:
        raise DraftOperationError("Dostawca o tej nazwie już istnieje")

    supplier.name = clean_name
    supplier.email = clean_email
    supplier.contact_person = contact_person.strip() or None
    supplier.phone = phone.strip() or None
    supplier.notes = notes.strip() or None
    supplier.active = active
    db.add(supplier)
    db.flush()
    return supplier


def assign_supplier(db: Session, complaint_id: int, supplier_id: int, *, actor: str) -> Complaint:
    complaint = db.get(Complaint, complaint_id)
    if complaint is None or complaint.merged_into_id is not None:
        raise DraftOperationError("Reklamacja nie istnieje")
    if complaint.status not in {
        ComplaintStatus.DRAFT,
        ComplaintStatus.PENDING_APPROVAL,
        ComplaintStatus.ACCEPTED,
        ComplaintStatus.READY_TO_SEND,
    }:
        raise DraftOperationError("Dostawcy nie można zmienić w tym statusie")
    supplier = db.get(Supplier, supplier_id)
    if supplier is None or not supplier.active:
        raise DraftOperationError("Wybierz aktywnego dostawcę")
    if db.scalar(select(EmailOutbox).where(EmailOutbox.complaint_id == complaint.id)) is not None:
        raise DraftOperationError("Nie można zmienić dostawcy po zleceniu wysyłki")

    previous_id = complaint.supplier_id
    complaint.supplier_id = supplier.id
    complaint.updated_at = func.now()
    draft = db.scalar(select(EmailDraft).where(EmailDraft.complaint_id == complaint.id))
    if draft is not None:
        draft.recipient = supplier.email
    if previous_id != supplier.id:
        db.add(
            ComplaintEvent(
                complaint_id=complaint.id,
                event_type="supplier_assigned",
                actor=actor,
                details={"from_supplier_id": previous_id, "to_supplier_id": supplier.id},
            )
        )
    return complaint


def default_subject(complaint: Complaint) -> str:
    material = str((complaint.approved_data or {}).get("material_product", "") or "")
    material = " ".join(material.splitlines()).strip()
    suffix = f" – {material}" if material else ""
    return f"Reklamacja {complaint.official_number}{suffix}"


def default_body(complaint: Complaint) -> str:
    data = complaint.approved_data or {}
    fields = (
        ("Materiał / produkt", data.get("material_product", "")),
        ("Ilość", data.get("quantity", "")),
        ("Opis wady", data.get("defect_description", "")),
        ("Numer zamówienia lub faktury", data.get("document_number", "")),
        ("Klient / projekt", data.get("customer_project", "")),
        ("Uwagi", data.get("notes", "")),
    )
    details = "\n".join(f"{label}: {value}" for label, value in fields if str(value).strip())
    attachment_note = "\n\nW załączeniu przesyłamy dokumentację fotograficzną."
    return (
        "Dzień dobry,\n\n"
        f"zgłaszamy reklamację nr {complaint.official_number}.\n\n"
        f"{details}{attachment_note}\n\n"
        "Prosimy o potwierdzenie przyjęcia reklamacji i informację o dalszym sposobie postępowania.\n\n"
        "Pozdrawiamy"
    )


def prepare_email_draft(db: Session, complaint_id: int, *, actor: str) -> EmailDraft:
    complaint = db.get(Complaint, complaint_id)
    if complaint is None or complaint.merged_into_id is not None:
        raise DraftOperationError("Reklamacja nie istnieje")
    if complaint.status != ComplaintStatus.ACCEPTED:
        raise DraftOperationError("Szkic można przygotować dla zaakceptowanej reklamacji")
    if complaint.supplier is None or not complaint.supplier.active:
        raise DraftOperationError("Najpierw przypisz aktywnego dostawcę")

    draft = db.scalar(select(EmailDraft).where(EmailDraft.complaint_id == complaint.id))
    if draft is None:
        draft = EmailDraft(
            complaint_id=complaint.id,
            recipient=complaint.supplier.email,
            subject=default_subject(complaint),
            body=default_body(complaint),
        )
        db.add(draft)
    complaint.status = ComplaintStatus.READY_TO_SEND
    complaint.updated_at = func.now()
    db.add(
        ComplaintEvent(
            complaint_id=complaint.id,
            event_type="email_draft_prepared",
            actor=actor,
            details={"supplier_id": complaint.supplier.id, "recipient": complaint.supplier.email},
        )
    )
    db.flush()
    return draft


def update_email_draft(
    db: Session,
    complaint_id: int,
    *,
    recipient: str,
    subject: str,
    body: str,
    actor: str,
) -> EmailDraft:
    complaint = db.get(Complaint, complaint_id)
    if complaint is None or complaint.status != ComplaintStatus.READY_TO_SEND:
        raise DraftOperationError("Szkicu nie można edytować w tym statusie")
    draft = db.scalar(select(EmailDraft).where(EmailDraft.complaint_id == complaint.id))
    if draft is None:
        raise DraftOperationError("Szkic wiadomości nie istnieje")
    if db.scalar(select(EmailOutbox).where(EmailOutbox.complaint_id == complaint.id)) is not None:
        raise DraftOperationError("Nie można zmienić szkicu po zleceniu wysyłki")
    clean_subject = subject.strip()
    clean_body = body.strip()
    if not clean_subject or not clean_body:
        raise DraftOperationError("Temat i treść wiadomości są wymagane")

    draft.recipient = validate_email(recipient)
    draft.subject = clean_subject.replace("\r", " ").replace("\n", " ")
    draft.body = clean_body
    complaint.updated_at = func.now()
    db.add(
        ComplaintEvent(
            complaint_id=complaint.id,
            event_type="email_draft_updated",
            actor=actor,
            details={},
        )
    )
    return draft


def selected_attachments(db: Session, complaint_id: int) -> list[Attachment]:
    return list(
        db.scalars(
            select(Attachment)
            .join(WhatsAppMessage, Attachment.whatsapp_message_id == WhatsAppMessage.id)
            .where(
                WhatsAppMessage.complaint_id == complaint_id,
                Attachment.include_in_email.is_(True),
            )
            .order_by(Attachment.id)
        ).all()
    )


def enqueue_smtp_email(
    db: Session,
    complaint_id: int,
    *,
    sender: str,
    actor: str,
) -> EmailOutbox:
    complaint = db.get(Complaint, complaint_id)
    if complaint is None or complaint.status != ComplaintStatus.READY_TO_SEND:
        raise DraftOperationError("Reklamacja nie jest gotowa do wysłania")
    draft = db.scalar(select(EmailDraft).where(EmailDraft.complaint_id == complaint.id))
    if draft is None:
        raise DraftOperationError("Szkic wiadomości nie istnieje")
    if db.scalar(select(EmailOutbox).where(EmailOutbox.complaint_id == complaint.id)) is not None:
        raise DraftOperationError("Wysyłka tej reklamacji została już zlecona")

    clean_sender = validate_email(sender)
    attachments = selected_attachments(db, complaint.id)
    sender_domain = clean_sender.rsplit("@", 1)[-1]
    item = EmailOutbox(
        complaint_id=complaint.id,
        recipient=draft.recipient,
        sender=clean_sender,
        subject=draft.subject,
        body=draft.body,
        attachments=[
            {
                "id": attachment.id,
                "filename": attachment.original_filename,
                "storage_path": attachment.storage_path,
                "mime_type": attachment.mime_type,
                "sha256": attachment.sha256,
                "size_bytes": attachment.size_bytes,
            }
            for attachment in attachments
        ],
        message_id=make_msgid(idstring=f"reklamacjeos-{complaint.id}", domain=sender_domain),
        status="pending",
    )
    db.add(item)
    db.add(
        ComplaintEvent(
            complaint_id=complaint.id,
            event_type="smtp_send_queued",
            actor=actor,
            details={"attachment_count": len(attachments)},
        )
    )
    db.flush()
    return item


def retry_smtp_email(db: Session, complaint_id: int, *, actor: str) -> EmailOutbox:
    item = db.scalar(select(EmailOutbox).where(EmailOutbox.complaint_id == complaint_id))
    if item is None:
        raise DraftOperationError("Brak zleconej wysyłki SMTP")
    if item.status != "failed":
        raise DraftOperationError("Ponowić można tylko jednoznacznie nieudaną wysyłkę")

    item.status = "pending"
    item.last_error = None
    item.claimed_at = None
    db.add(
        ComplaintEvent(
            complaint_id=complaint_id,
            event_type="smtp_send_retry_requested",
            actor=actor,
            details={"outbox_id": item.id},
        )
    )
    return item


def mark_manually_sent(db: Session, complaint_id: int, *, actor: str) -> EmailSent:
    complaint = db.get(Complaint, complaint_id)
    if complaint is None or complaint.status != ComplaintStatus.READY_TO_SEND:
        raise DraftOperationError("Reklamacja nie jest gotowa do oznaczenia jako wysłana")
    draft = db.scalar(select(EmailDraft).where(EmailDraft.complaint_id == complaint.id))
    if draft is None:
        raise DraftOperationError("Szkic wiadomości nie istnieje")
    outbox = db.scalar(select(EmailOutbox).where(EmailOutbox.complaint_id == complaint.id))
    if outbox is not None and outbox.status in {"pending", "processing", "sent"}:
        raise DraftOperationError("Trwa lub zakończyła się automatyczna wysyłka SMTP")

    attachments = selected_attachments(db, complaint.id)
    sent = EmailSent(
        complaint_id=complaint.id,
        recipient=draft.recipient,
        subject=draft.subject,
        body=draft.body,
        attachments=[
            {
                "id": attachment.id,
                "filename": attachment.original_filename,
                "sha256": attachment.sha256,
                "size_bytes": attachment.size_bytes,
            }
            for attachment in attachments
        ],
        message_id=None,
        smtp_result={"mode": "manual"},
        sent_at=datetime.now().astimezone(),
    )
    db.add(sent)
    complaint.status = ComplaintStatus.SENT
    complaint.updated_at = func.now()
    db.add(
        ComplaintEvent(
            complaint_id=complaint.id,
            event_type="complaint_marked_sent",
            actor=actor,
            details={"recipient": draft.recipient, "attachment_count": len(attachments)},
        )
    )
    db.flush()
    return sent
