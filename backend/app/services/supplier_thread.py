from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Complaint, ComplaintEvent, WhatsAppMessage
from .complaints import BUSINESS_DECISIONS
from .grouping import DraftOperationError

REF_RE = re.compile(r"<[^>]+>")
REPLY_PREFIX_RE = re.compile(r"^\s*(?:(?:re|fw|fwd|odp|pd)\s*:\s*)+", re.I)
SPACE_RE = re.compile(r"\s+")
ZS_RE = re.compile(r"\bZS[\s-]?(\d{3,6})(?:[/-]\d+)?", re.I)


def normalize_subject(value: str | None) -> str:
    text = REPLY_PREFIX_RE.sub("", value or "")
    text = SPACE_RE.sub(" ", text).strip().lower()
    return text.rstrip(" .:-")


def normalize_zs(value: str | None) -> str | None:
    match = ZS_RE.search(value or "")
    return f"ZS-{match.group(1)}" if match else None


def reference_ids(*values: str | None) -> set[str]:
    refs: set[str] = set()
    for value in values:
        if not value:
            continue
        found = REF_RE.findall(value)
        if found:
            refs.update(found)
        elif "@" in value:
            refs.add(value.strip())
    return refs


def _email_payload(message: WhatsAppMessage) -> dict[str, Any]:
    payload = message.source_payload or {}
    return payload if isinstance(payload, dict) else {}


def email_direction(message: WhatsAppMessage) -> str:
    payload = _email_payload(message)
    explicit = str(payload.get("direction") or "").lower()
    if explicit in {"incoming", "outgoing"}:
        return explicit
    folder = str(payload.get("folder") or "").lower()
    return "outgoing" if "sent" in folder else "incoming"


def complaint_email_thread(complaint: Complaint) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for message in sorted(complaint.messages, key=lambda item: (item.source_timestamp, item.id)):
        payload = _email_payload(message)
        if payload.get("source") != "email" and message.message_type != "email":
            continue
        rows.append(
            {
                "id": message.id,
                "source_id": message.wa_message_id,
                "direction": email_direction(message),
                "sender": str(payload.get("sender") or message.author_name or ""),
                "recipients": str(payload.get("recipients") or ""),
                "subject": str(payload.get("subject") or ""),
                "body": message.body or "",
                "message_id": str(payload.get("message_id") or ""),
                "in_reply_to": str(payload.get("in_reply_to") or ""),
                "references": str(payload.get("references") or ""),
                "timestamp": message.source_timestamp,
                "attachments": list(message.attachments),
            }
        )
    return rows


def latest_supplier_email(complaint: Complaint) -> dict[str, Any] | None:
    incoming = [row for row in complaint_email_thread(complaint) if row["direction"] == "incoming"]
    return incoming[-1] if incoming else None


def latest_outgoing_email(complaint: Complaint) -> dict[str, Any] | None:
    outgoing = [row for row in complaint_email_thread(complaint) if row["direction"] == "outgoing"]
    return outgoing[-1] if outgoing else None


def _active_complaints(db: Session) -> list[Complaint]:
    return db.scalars(
        select(Complaint)
        .where(Complaint.merged_into_id.is_(None))
        .order_by(Complaint.updated_at.desc(), Complaint.id.desc())
        .limit(500)
    ).all()


def find_complaint_for_email(
    db: Session,
    *,
    in_reply_to: str | None,
    references: str | None,
    subject: str,
    body: str,
    source_timestamp: datetime,
    sender: str = "",
) -> tuple[Complaint | None, str]:
    refs = reference_ids(in_reply_to, references)
    email_messages = db.scalars(
        select(WhatsAppMessage)
        .where(WhatsAppMessage.message_type == "email")
        .order_by(WhatsAppMessage.source_timestamp.desc(), WhatsAppMessage.id.desc())
        .limit(2000)
    ).all()

    if refs:
        for message in email_messages:
            payload = _email_payload(message)
            msgid = str(payload.get("message_id") or "").strip()
            if msgid and msgid in refs and message.complaint_id:
                complaint = db.get(Complaint, message.complaint_id)
                if complaint and complaint.merged_into_id is None:
                    return complaint, "header_reference"

    incoming_zs = normalize_zs(f"{subject}\n{body}")
    if incoming_zs:
        matches = []
        for complaint in _active_complaints(db):
            card = complaint.approved_data or {}
            card_zs = normalize_zs(str(card.get("paneltech_order_number") or ""))
            if card_zs == incoming_zs:
                matches.append(complaint)
        if len(matches) == 1:
            return matches[0], "paneltech_order_number"
        if matches:
            return matches[0], "paneltech_order_number_recent"

    normalized = normalize_subject(subject)
    if normalized:
        candidates: list[tuple[datetime, Complaint]] = []
        for message in email_messages:
            if email_direction(message) != "outgoing" or not message.complaint_id:
                continue
            if message.source_timestamp > source_timestamp:
                continue
            payload = _email_payload(message)
            if normalize_subject(str(payload.get("subject") or "")) != normalized:
                continue
            complaint = db.get(Complaint, message.complaint_id)
            if complaint and complaint.merged_into_id is None:
                candidates.append((message.source_timestamp, complaint))
        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            return candidates[0][1], "normalized_subject"

    # Last-resort supplier-domain match is only allowed when the subject still clearly says
    # "reklamacja"; it must never bind generic supplier traffic to a complaint.
    if "reklamac" in (subject or "").lower() and "@" in sender:
        sender_domain = sender.lower().rsplit("@", 1)[-1].strip(" >")
        candidates: list[tuple[datetime, Complaint]] = []
        for message in email_messages:
            if email_direction(message) != "outgoing" or not message.complaint_id:
                continue
            payload = _email_payload(message)
            recipients = str(payload.get("recipients") or "").lower()
            if sender_domain and sender_domain not in recipients:
                continue
            complaint = db.get(Complaint, message.complaint_id)
            if complaint and complaint.merged_into_id is None and message.source_timestamp <= source_timestamp:
                candidates.append((message.source_timestamp, complaint))
        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            return candidates[0][1], "supplier_domain_recent"

    return None, "unmatched"


def latest_reply_draft(complaint: Complaint, latest_source_id: str | None = None) -> dict[str, Any] | None:
    for event in sorted(complaint.events, key=lambda item: (item.created_at, item.id), reverse=True):
        if event.event_type not in {"supplier_reply_draft_generated", "supplier_reply_draft_edited"}:
            continue
        details = event.details or {}
        if latest_source_id and details.get("reply_to_source_id") != latest_source_id:
            continue
        body = str(details.get("body") or "").strip()
        if not body:
            continue
        return {
            "body": body,
            "provider": str(details.get("provider") or "manual"),
            "model": str(details.get("model") or ""),
            "fallback": bool(details.get("fallback", False)),
            "created_at": event.created_at,
            "event_type": event.event_type,
            "reply_to_source_id": details.get("reply_to_source_id"),
        }
    return None


def _ai_request(payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        settings.reply_ai_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.reply_ai_timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Generator odpowiedzi jest niedostępny ({type(exc).__name__})") from exc


def generate_supplier_reply(
    db: Session,
    complaint: Complaint,
    *,
    actor: str,
    incoming: dict[str, Any] | None = None,
) -> dict[str, Any]:
    latest = incoming or latest_supplier_email(complaint)
    if latest is None:
        raise DraftOperationError("Brak odpowiedzi dostawcy, do której można przygotować szkic")

    decision = BUSINESS_DECISIONS.get(complaint.resolution_strategy) if complaint.resolution_strategy else None
    if complaint.resolution_strategy and decision is None:
        raise DraftOperationError("Nieznana decyzja biznesowa")

    thread = complaint_email_thread(complaint)
    last_outgoing = latest_outgoing_email(complaint)
    business_position = ""
    if last_outgoing is not None and last_outgoing["timestamp"] <= latest["timestamp"]:
        business_position = last_outgoing["body"]
    payload = {
        "complaint_label": complaint.official_number or complaint.draft_number,
        "supplier_name": complaint.supplier.name if complaint.supplier else "Paneltech",
        "resolution_strategy": complaint.resolution_strategy or "",
        "business_position": business_position,
        "discount_percent": complaint.resolution_discount_percent,
        "approved_card": complaint.approved_data or {},
        "latest_supplier_message": latest["body"],
        "thread": [
            {
                "direction": row["direction"],
                "sender": row["sender"],
                "subject": row["subject"],
                "body": row["body"],
                "timestamp": row["timestamp"].isoformat(),
            }
            for row in thread[-12:]
        ],
    }
    result = _ai_request(payload)
    body = str(result.get("body") or "").strip()
    if not body:
        raise RuntimeError("Generator odpowiedzi zwrócił pusty szkic")

    details = {
        "body": body,
        "provider": str(result.get("provider") or "unknown"),
        "model": str(result.get("model") or ""),
        "fallback": bool(result.get("fallback", False)),
        "reply_to_source_id": latest["source_id"],
        "decision": complaint.resolution_strategy,
        "decision_label": decision["label"] if decision else "Stanowisko z ostatniej wiadomości użytkownika",
    }
    db.add(
        ComplaintEvent(
            complaint_id=complaint.id,
            event_type="supplier_reply_draft_generated",
            actor=actor,
            details=details,
        )
    )
    return details


def save_supplier_reply_draft(
    db: Session,
    complaint: Complaint,
    *,
    body: str,
    actor: str,
) -> dict[str, Any]:
    latest = latest_supplier_email(complaint)
    clean = body.strip()
    if latest is None:
        raise DraftOperationError("Brak odpowiedzi dostawcy")
    if not clean:
        raise DraftOperationError("Treść proponowanej odpowiedzi nie może być pusta")
    details = {
        "body": clean,
        "provider": "manual",
        "model": "",
        "fallback": False,
        "reply_to_source_id": latest["source_id"],
        "decision": complaint.resolution_strategy,
    }
    db.add(
        ComplaintEvent(
            complaint_id=complaint.id,
            event_type="supplier_reply_draft_edited",
            actor=actor,
            details=details,
        )
    )
    return details
