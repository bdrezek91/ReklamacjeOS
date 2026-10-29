from __future__ import annotations

import re
from typing import Any

from ..models import Complaint, ComplaintStatus
from .intake import missing_required_fields
from .supplier_thread import complaint_email_thread, latest_outgoing_email, latest_reply_draft, latest_supplier_email

SIGNATURE_MARKERS = (
    "\n--\n",
    "\nZ wyrazami szacunku",
    "\nPozdrawiam",
    "\nINFORMACJA RODO",
    "\nUwaga: Niniejsza wiadomość",
)


def _clean_excerpt(value: str | None, limit: int = 900) -> str:
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    for marker in SIGNATURE_MARKERS:
        idx = text.lower().find(marker.lower())
        if idx > 40:
            text = text[:idx].strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip() + "…"
    return text


def derive_autopilot(complaint: Complaint) -> dict[str, Any]:
    thread = complaint_email_thread(complaint)
    latest_supplier = latest_supplier_email(complaint)
    latest_outgoing = latest_outgoing_email(complaint)
    last_email = thread[-1] if thread else None
    missing = missing_required_fields(complaint)
    latest_draft = latest_reply_draft(
        complaint,
        latest_supplier["source_id"] if latest_supplier else None,
    )

    if complaint.merged_into_id is not None or complaint.status == ComplaintStatus.CLOSED:
        bucket = "closed"
        label = "Zamknięte"
        headline = "Sprawa zakończona"
        next_action = "Nie wymaga działania."
        tone = "closed"
    elif last_email is not None and last_email["direction"] == "outgoing":
        bucket = "waiting"
        label = "Czekamy"
        headline = "Czekamy na odpowiedź dostawcy"
        next_action = "Nic nie musisz robić. System monitoruje skrzynkę i przypnie kolejną odpowiedź automatycznie."
        tone = "waiting"
    elif last_email is not None and last_email["direction"] == "incoming":
        bucket = "action"
        label = "Do zrobienia"
        headline = "Dostawca odpisał"
        next_action = (
            "Sprawdź proponowaną odpowiedź i skopiuj ją do maila."
            if latest_draft
            else "System przygotuje propozycję odpowiedzi na podstawie całego wątku."
        )
        tone = "action"
    elif complaint.status == ComplaintStatus.READY_TO_SEND:
        bucket = "action"
        label = "Do zrobienia"
        headline = "Reklamacja gotowa do wysłania"
        next_action = "Sprawdź przygotowaną wiadomość i wyślij ją do dostawcy."
        tone = "action"
    elif complaint.status == ComplaintStatus.SENT:
        bucket = "waiting"
        label = "Czekamy"
        headline = "Reklamacja wysłana"
        next_action = "Czekamy na odpowiedź dostawcy."
        tone = "waiting"
    elif missing:
        bucket = "action"
        label = "Do zrobienia"
        headline = "Brakuje danych"
        next_action = "Uzupełnij brakujące informacje. Reszta obsługi wykona się automatycznie."
        tone = "action"
    elif complaint.status in {ComplaintStatus.DRAFT, ComplaintStatus.PENDING_APPROVAL}:
        bucket = "action"
        label = "Do zrobienia"
        headline = "Sprawdź zgłoszenie"
        next_action = "Zweryfikuj podstawowe dane. Zaawansowane narzędzia są dostępne tylko w razie potrzeby."
        tone = "action"
    else:
        bucket = "action"
        label = "Do zrobienia"
        headline = "Sprawa wymaga sprawdzenia"
        next_action = "Otwórz reklamację i sprawdź ostatnią aktywność."
        tone = "action"

    last_move = None
    if last_email is not None:
        who = "Ty" if last_email["direction"] == "outgoing" else (complaint.supplier.name if complaint.supplier else "Dostawca")
        last_move = {
            "who": who,
            "direction": last_email["direction"],
            "timestamp": last_email["timestamp"],
            "subject": last_email["subject"],
            "excerpt": _clean_excerpt(last_email["body"], 500),
        }

    current_position = _clean_excerpt(latest_outgoing["body"], 1100) if latest_outgoing else ""
    supplier_position = _clean_excerpt(latest_supplier["body"], 1100) if latest_supplier else ""

    card = complaint.approved_data or {}
    case_bits = []
    if card.get("paneltech_order_number"):
        case_bits.append(str(card["paneltech_order_number"]))
    if card.get("material_product"):
        case_bits.append(str(card["material_product"]))
    if card.get("quantity"):
        case_bits.append(f'{card["quantity"]} szt.')
    if card.get("dimensions"):
        case_bits.append(str(card["dimensions"]))

    return {
        "bucket": bucket,
        "label": label,
        "headline": headline,
        "next_action": next_action,
        "tone": tone,
        "needs_reply": bool(last_email and last_email["direction"] == "incoming"),
        "last_email": last_email,
        "last_move": last_move,
        "latest_supplier": latest_supplier,
        "latest_outgoing": latest_outgoing,
        "reply_draft": latest_draft,
        "current_position": current_position,
        "supplier_position": supplier_position,
        "case_line": " · ".join(case_bits),
        "defect": _clean_excerpt(str(card.get("defect_description") or ""), 360),
        "missing_fields": missing,
        "thread_count": len(thread),
    }


def sort_key(view: dict[str, Any]) -> tuple[int, float]:
    bucket_rank = {"action": 0, "waiting": 1, "closed": 2}.get(view["autopilot"]["bucket"], 3)
    complaint = view["complaint"]
    updated = complaint.updated_at.timestamp() if complaint.updated_at else 0.0
    return bucket_rank, -updated
