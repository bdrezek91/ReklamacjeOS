import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Complaint, ComplaintEvent, ComplaintField, WhatsAppOutbox

DEFECT_WORDS = (
    "uszkodz", "wgniec", "porys", "rysa", "zamek", "odcie", "piank",
    "grad", "pęk", "pek", "odkle", "deform", "wada", "reklamac", "zarys",
)
FIELD_LABELS = {
    "quantity": "ilość sztuk",
    "dimensions": "wymiary płyt",
    "defect_description": "opis wady",
    "supplier_identifier": "nr zamówienia Paneltech lub nr paczki",
}


def _clean(value: str) -> str:
    return " ".join(value.replace("×", "x").split()).strip(" ,.;:")


def extract_fields_from_text(text: str) -> dict[str, str]:
    source = text or ""
    result: dict[str, str] = {}
    quantity = re.search(r"\b(\d{1,4})\s*(?:szt\.?|sztuk(?:i)?|płyt(?:y)?)\b", source, re.IGNORECASE)
    if quantity:
        result["quantity"] = f"{quantity.group(1)} szt."

    dimensions = re.search(
        r"\b(\d{2,5})\s*[x×]\s*(\d{2,5})(?:\s*[x×]\s*(\d{1,5}))?\s*(?:mm|cm|m)?\b",
        source,
        re.IGNORECASE,
    )
    if dimensions:
        parts = [part for part in dimensions.groups() if part]
        result["dimensions"] = " x ".join(parts)

    order = re.search(r"\bZS[\s-]?(\d{3,6})\b", source, re.IGNORECASE)
    if order:
        result["paneltech_order_number"] = f"ZS-{order.group(1)}".upper()

    package = re.search(
        r"\b(?:nr\s*)?paczk(?:a|i|ę|e)\s*(?:nr\s*)?[:#-]?\s*([A-Z0-9][A-Z0-9/_-]{2,})",
        source,
        re.IGNORECASE,
    )
    if package:
        result["package_number"] = package.group(1).upper()
    material = re.search(
        r"\b(płyta\s+(?:dachowa|ścienna)|panel\s+(?:dachowy|ścienny)|PIR\s*\d{2,3})\b",
        source,
        re.IGNORECASE,
    )
    if material:
        result["material_product"] = _clean(material.group(1))

    sentences = [part.strip() for part in re.split(r"[\n.!?]+", source) if part.strip()]
    defect_sentences = [
        sentence for sentence in sentences
        if any(word in sentence.lower() for word in DEFECT_WORDS)
    ]
    if defect_sentences:
        result["defect_description"] = _clean(" ".join(defect_sentences[:2]))[:700]
    return result


def merge_extracted_fields(
    db: Session,
    complaint: Complaint,
    extracted: dict[str, str],
    *,
    source: str,
    confidence: int,
) -> list[str]:
    current = dict(complaint.approved_data or {})
    changed: list[str] = []
    for field_name, value in extracted.items():
        if not value or str(current.get(field_name, "")).strip():
            continue
        current[field_name] = value
        changed.append(field_name)
        db.add(
            ComplaintField(
                complaint_id=complaint.id,
                field_name=field_name,
                source=source,
                value={"value": value},
                confidence=confidence,
                is_uncertain=confidence < 80,
            )
        )
    if changed:
        complaint.approved_data = current
        complaint.updated_at = func.now()
        db.add(
            ComplaintEvent(
                complaint_id=complaint.id,
                event_type="complaint_card_autofilled",
                actor=f"system:{source}",
                details={"fields": changed},
            )
        )
    return changed


def missing_required_fields(complaint: Complaint) -> list[str]:
    data = complaint.approved_data or {}
    missing: list[str] = []
    for field_name in ("quantity", "dimensions", "defect_description"):
        if not str(data.get(field_name, "")).strip():
            missing.append(field_name)
    supplier_path = complaint.resolution_strategy in {
        "return_to_supplier", "keep_request_discount", "second_grade_50"
    }
    if supplier_path and not (
        str(data.get("paneltech_order_number", "")).strip()
        or str(data.get("package_number", "")).strip()
    ):
        missing.append("supplier_identifier")
    return missing


def ensure_missing_data_question(
    db: Session,
    complaint: Complaint,
    *,
    group_id: str,
) -> WhatsAppOutbox | None:
    missing = missing_required_fields(complaint)
    if not missing or not group_id:
        return None
    existing = db.scalar(
        select(WhatsAppOutbox).where(
            WhatsAppOutbox.complaint_id == complaint.id,
            WhatsAppOutbox.message_kind == "missing_data",
        )
    )
    if existing is not None:
        return existing
    label = complaint.official_number or f"Reklamacja {complaint.id:04d}"
    readable = "; ".join(FIELD_LABELS[item] for item in missing)
    body = (
        f"{label} — brakuje danych do dalszej obsługi: {readable}. "
        "Proszę uzupełnić je w jednej wiadomości i, jeśli jest dostępna, dodać zdjęcie etykiety/paczki."
    )
    item = WhatsAppOutbox(
        complaint_id=complaint.id,
        group_id=group_id,
        body=body,
        message_kind="missing_data",
        status="pending",
    )
    db.add(item)
    db.add(
        ComplaintEvent(
            complaint_id=complaint.id,
            event_type="missing_data_question_queued",
            actor="system:intake",
            details={"missing_fields": missing},
        )
    )
    db.flush()
    return item
