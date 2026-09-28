from collections.abc import Iterable
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Complaint, ComplaintEvent, ComplaintStatus, WhatsAppMessage


class DraftOperationError(ValueError):
    pass


def create_draft(
    db: Session,
    *,
    actor: str,
    rule: str,
    trigger_message_id: int | None = None,
) -> Complaint:
    complaint = Complaint(
        draft_number=f"PENDING-{uuid4().hex}",
        status=ComplaintStatus.DRAFT,
    )
    db.add(complaint)
    db.flush()
    complaint.draft_number = f"DRAFT-{complaint.id:04d}"
    db.add(
        ComplaintEvent(
            complaint_id=complaint.id,
            event_type="draft_created",
            actor=actor,
            details={"rule": rule, "trigger_message_id": trigger_message_id},
        )
    )
    return complaint


def _active_complaint(db: Session, complaint_id: int | None) -> Complaint | None:
    seen: set[int] = set()
    while complaint_id and complaint_id not in seen:
        seen.add(complaint_id)
        complaint = db.get(Complaint, complaint_id)
        if complaint is None:
            return None
        if complaint.merged_into_id is None:
            return complaint
        complaint_id = complaint.merged_into_id
    return None


def assign_message_to_draft(
    db: Session,
    message: WhatsAppMessage,
    *,
    actor: str = "system:grouping",
) -> tuple[Complaint, str]:
    current = _active_complaint(db, message.complaint_id)
    if current is not None:
        return current, "already_assigned"

    complaint: Complaint | None = None
    rule = "new_draft"

    if message.quoted_message_id:
        quoted = db.scalar(
            select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == message.quoted_message_id)
        )
        if quoted is not None:
            complaint = _active_complaint(db, quoted.complaint_id)
            if complaint is not None:
                rule = "quoted_message"

    if complaint is None and message.author_id:
        cutoff = message.source_timestamp - timedelta(minutes=settings.grouping_window_minutes)
        recent = db.scalar(
            select(WhatsAppMessage)
            .join(Complaint, WhatsAppMessage.complaint_id == Complaint.id)
            .where(
                WhatsAppMessage.id != message.id,
                WhatsAppMessage.group_id == message.group_id,
                WhatsAppMessage.author_id == message.author_id,
                WhatsAppMessage.source_timestamp >= cutoff,
                WhatsAppMessage.source_timestamp <= message.source_timestamp,
                Complaint.status == ComplaintStatus.DRAFT,
                Complaint.merged_into_id.is_(None),
            )
            .order_by(WhatsAppMessage.source_timestamp.desc(), WhatsAppMessage.id.desc())
            .limit(1)
        )
        if recent is not None:
            complaint = _active_complaint(db, recent.complaint_id)
            if complaint is not None:
                rule = "same_author_time_window"

    if complaint is None:
        complaint = create_draft(
            db,
            actor=actor,
            rule="automatic",
            trigger_message_id=message.id,
        )

    message.complaint_id = complaint.id
    complaint.updated_at = func.now()
    db.add(
        ComplaintEvent(
            complaint_id=complaint.id,
            event_type="message_assigned",
            actor=actor,
            details={"message_id": message.id, "rule": rule},
        )
    )
    return complaint, rule


def backfill_unassigned_messages(db: Session) -> int:
    messages = db.scalars(
        select(WhatsAppMessage)
        .where(WhatsAppMessage.complaint_id.is_(None))
        .order_by(WhatsAppMessage.source_timestamp, WhatsAppMessage.id)
    ).all()
    for message in messages:
        assign_message_to_draft(db, message, actor="system:backfill")
    return len(messages)


def merge_drafts(db: Session, source_id: int, target_id: int, *, actor: str) -> Complaint:
    if source_id == target_id:
        raise DraftOperationError("Nie można scalić draftu z nim samym")

    source = _active_complaint(db, source_id)
    target = _active_complaint(db, target_id)
    if source is None or source.id != source_id:
        raise DraftOperationError("Draft źródłowy nie jest aktywny")
    if target is None or target.id != target_id:
        raise DraftOperationError("Draft docelowy nie jest aktywny")
    if source.status != ComplaintStatus.DRAFT or target.status != ComplaintStatus.DRAFT:
        raise DraftOperationError("Można scalać wyłącznie aktywne drafty")

    messages = db.scalars(
        select(WhatsAppMessage)
        .where(WhatsAppMessage.complaint_id == source.id)
        .order_by(WhatsAppMessage.source_timestamp, WhatsAppMessage.id)
    ).all()
    message_ids = [message.id for message in messages]
    for message in messages:
        message.complaint_id = target.id

    source.merged_into_id = target.id
    source.updated_at = func.now()
    target.updated_at = func.now()
    db.add_all(
        [
            ComplaintEvent(
                complaint_id=source.id,
                event_type="draft_merged_into",
                actor=actor,
                details={"target_complaint_id": target.id, "message_ids": message_ids},
            ),
            ComplaintEvent(
                complaint_id=target.id,
                event_type="draft_merged",
                actor=actor,
                details={"source_complaint_id": source.id, "message_ids": message_ids},
            ),
        ]
    )
    return target


def split_draft(
    db: Session,
    source_id: int,
    message_ids: Iterable[int],
    *,
    actor: str,
) -> Complaint:
    selected_ids = sorted(set(message_ids))
    if not selected_ids:
        raise DraftOperationError("Wybierz co najmniej jedną wiadomość")

    source = _active_complaint(db, source_id)
    if source is None or source.id != source_id or source.status != ComplaintStatus.DRAFT:
        raise DraftOperationError("Draft źródłowy nie jest aktywny")

    all_messages = db.scalars(
        select(WhatsAppMessage)
        .where(WhatsAppMessage.complaint_id == source.id)
        .order_by(WhatsAppMessage.source_timestamp, WhatsAppMessage.id)
    ).all()
    by_id = {message.id: message for message in all_messages}
    if any(message_id not in by_id for message_id in selected_ids):
        raise DraftOperationError("Wybrana wiadomość nie należy do tego draftu")
    if len(selected_ids) >= len(all_messages):
        raise DraftOperationError("W źródłowym drafcie musi pozostać co najmniej jedna wiadomość")

    target = create_draft(db, actor=actor, rule="manual_split")
    for message_id in selected_ids:
        by_id[message_id].complaint_id = target.id

    source.updated_at = func.now()
    target.updated_at = func.now()
    db.add_all(
        [
            ComplaintEvent(
                complaint_id=source.id,
                event_type="messages_split_out",
                actor=actor,
                details={"target_complaint_id": target.id, "message_ids": selected_ids},
            ),
            ComplaintEvent(
                complaint_id=target.id,
                event_type="messages_split_in",
                actor=actor,
                details={"source_complaint_id": source.id, "message_ids": selected_ids},
            ),
        ]
    )
    return target
