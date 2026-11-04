"""Explicit supplier-offer acceptance workflow. Never sends without a panel POST."""
from __future__ import annotations

import re
from email.utils import make_msgid, parseaddr
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..config import settings
from ..models import Complaint, ComplaintEvent, ComplaintStatus, SupplierReplyOutbox
from .correspondence import validate_email
from .grouping import DraftOperationError
from .signature import compose_body
from .supplier_thread import latest_supplier_email, normalize_zs

DISCOUNT_OFFER = re.compile(r"\b(\d{1,3})\s*%\s*(?:rabatu|rabat|obniżki|upustu)\b", re.I)


def offered_discount(message: str) -> int | None:
    match = DISCOUNT_OFFER.search(message or "")
    if not match:
        return None
    number = int(match.group(1))
    return number if 1 <= number <= 100 else None


def default_acceptance_body(complaint: Complaint, incoming: dict, percent: int) -> str:
    card = complaint.approved_data or {}
    quantity = (str(card.get("quantity") or "").strip() or "zgłoszone")
    order = normalize_zs(incoming.get("body") or "") or normalize_zs(str(card.get("paneltech_order_number") or ""))
    subject = f"{quantity} płyt dachowych"
    order_phrase = f" z zamówienia {order}" if order else ""
    return (
        "Dzień dobry,\n\n"
        f"w nawiązaniu do Państwa odpowiedzi w sprawie reklamacji {complaint.official_number} "
        f"potwierdzamy akceptację zaproponowanego rabatu {percent}% na {subject}{order_phrase}.\n\n"
        "Prosimy o wystawienie i przesłanie odpowiedniej korekty faktury.\n\n"
        "Dziękujemy za rozpatrzenie reklamacji."
    )


def reply_status(db: Session, complaint: Complaint, source_id: str | None = None) -> SupplierReplyOutbox | None:
    query = select(SupplierReplyOutbox).where(SupplierReplyOutbox.complaint_id == complaint.id)
    if source_id:
        query = query.where(SupplierReplyOutbox.reply_to_source_id == source_id)
    return db.scalar(query.order_by(SupplierReplyOutbox.id.desc()).limit(1))


def enqueue_supplier_acceptance(
    db: Session,
    complaint_id: int,
    *,
    reply_to_source_id: str,
    discount_percent: int,
    body: str,
    actor: str,
) -> SupplierReplyOutbox:
    complaint = db.get(Complaint, complaint_id)
    if complaint is None or complaint.merged_into_id is not None:
        raise DraftOperationError("Reklamacja nie istnieje albo jest scalona")
    if complaint.status != ComplaintStatus.SENT or not complaint.official_number:
        raise DraftOperationError("Potwierdzenie oferty można wysłać tylko dla reklamacji już zgłoszonej dostawcy")
    if not settings.smtp_configured:
        raise DraftOperationError("Poczta SMTP nie jest gotowa")
    incoming = latest_supplier_email(complaint)
    if not incoming or incoming["source_id"] != reply_to_source_id:
        raise DraftOperationError("Odpowiedź Paneltech zmieniła się. Odśwież kartę.")
    offered = offered_discount(incoming["body"])
    if offered is None or offered != discount_percent:
        raise DraftOperationError("Oferta rabatu nie została jednoznacznie rozpoznana. Nie wysłano potwierdzenia.")
    existing = reply_status(db, complaint, reply_to_source_id)
    if existing is not None:
        raise DraftOperationError("Potwierdzenie tej oferty już zostało zlecone. Nie wysyłamy duplikatu.")
    recipient = validate_email(parseaddr(incoming["sender"])[1])
    if complaint.supplier is None or recipient.rsplit("@", 1)[-1].lower() != complaint.supplier.email.rsplit("@", 1)[-1].lower():
        raise DraftOperationError("Nadawca wiadomości nie pasuje do dostawcy. Sprawdź korespondencję.")
    text = body.strip()
    if not text or f"{offered}%" not in text:
        raise DraftOperationError("Treść potwierdzenia musi wyraźnie wskazywać zaakceptowany rabat.")
    if len(text) > 10000:
        raise DraftOperationError("Wiadomość jest zbyt długa")
    incoming_mid = str(incoming.get("message_id") or "").strip()
    if not (incoming_mid.startswith("<") and incoming_mid.endswith(">")):
        raise DraftOperationError("Brak prawidłowego Message-ID odpowiedzi Paneltech.")
    refs = str(incoming.get("references") or "").strip()
    if incoming_mid not in refs.split():
        refs = f"{refs} {incoming_mid}".strip()
    original_subject = re.sub(r"[\r\n]+", " ", str(incoming["subject"] or "")).strip()
    subject = original_subject if original_subject.lower().startswith("re:") else f"RE: {original_subject}"
    item = SupplierReplyOutbox(
        complaint_id=complaint.id,
        reply_to_source_id=reply_to_source_id,
        recipient=recipient,
        sender=validate_email(settings.smtp_from_address),
        subject=subject,
        body=compose_body(text),
        in_reply_to=incoming_mid,
        references=refs,
        message_id=make_msgid(idstring=f"reklamacjeos-accept-{complaint.id}", domain=settings.smtp_from_address.rsplit("@",1)[-1]),
        discount_percent=discount_percent,
        status="pending",
    )
    db.add(item)
    db.flush()
    db.add(ComplaintEvent(
        complaint_id=complaint.id,
        event_type="supplier_offer_acceptance_queued",
        actor=actor,
        details={"source_id": reply_to_source_id, "discount_percent": discount_percent, "reply_outbox_id": item.id},
    ))
    return item
