import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from typesafe_sdk import Noul, Score, TypeSafeClient

from ..config import settings
from ..models import Attachment, Complaint, ComplaintEvent, JevAssessment, WhatsAppMessage
from .grouping import DraftOperationError


class JevResponse(Protocol):
    def model_dump(self, *, mode: str) -> dict[str, Any]: ...


class JevClient(Protocol):
    def system_one(self, state: Any, questions: dict[str, Any], *, model: str) -> JevResponse: ...


QUESTION_LABELS = {
    "is_complaint": "Czy treść opisuje reklamację?",
    "messages_coherent": "Czy wiadomości dotyczą jednego zgłoszenia?",
    "urgency": "Pilność",
    "evidence_quality": "Jakość informacji i dowodów",
    "has_material_product": "Materiał / produkt możliwy do ustalenia",
    "has_quantity": "Ilość możliwa do ustalenia",
    "has_defect_description": "Opis wady możliwy do ustalenia",
    "has_document_number": "Numer dokumentu możliwy do ustalenia",
    "has_customer_project": "Klient / projekt możliwy do ustalenia",
    "needs_human_review": "Wymaga szczególnej uwagi operatora",
}


def build_jev_state(complaint: Complaint) -> dict[str, Any]:
    messages = sorted(complaint.messages, key=lambda message: (message.source_timestamp, message.id))
    return {
        "complaint": {
            "draft_number": complaint.draft_number,
            "official_number": complaint.official_number,
            "status": complaint.status.value,
            "approved_card": complaint.approved_data or {},
        },
        "messages": [
            {
                "author_name": message.author_name or "",
                "body": message.body[:4000],
                "message_type": message.message_type,
                "source_timestamp": message.source_timestamp.isoformat(),
                "attachment_count": len(message.attachments),
            }
            for message in messages[:50]
        ],
        "ocr": [
            {
                "attachment_id": attachment.id,
                "text": (
                    attachment.ocr_result.corrected_text
                    if attachment.ocr_result.corrected_text is not None
                    else attachment.ocr_result.raw_text
                )[:4000],
                "confidence": attachment.ocr_result.confidence,
                "reviewed": attachment.ocr_result.reviewed_at is not None,
            }
            for message in messages
            for attachment in message.attachments
            if attachment.ocr_result is not None and attachment.ocr_result.status == "completed"
        ],
    }


def jev_questions() -> dict[str, Any]:
    return {
        "is_complaint": Noul(instructions="Treść opisuje reklamację materiału lub produktu."),
        "messages_coherent": Noul(instructions="Wszystkie wiadomości dotyczą tego samego zgłoszenia."),
        "urgency": Score(
            instructions="Oceń pilność obsługi reklamacji.",
            criteria=["Brak oznak pilności", "Wymaga szybkiej obsługi", "Wymaga pilnej reakcji"],
        ),
        "evidence_quality": Score(
            instructions="Oceń kompletność informacji i dowodów potrzebnych do obsługi reklamacji.",
            criteria=["Niewystarczające", "Częściowe", "Wystarczające"],
        ),
        "has_material_product": Noul(instructions="Można jednoznacznie ustalić reklamowany materiał lub produkt."),
        "has_quantity": Noul(instructions="Można jednoznacznie ustalić reklamowaną ilość."),
        "has_defect_description": Noul(instructions="Można jednoznacznie ustalić opis wady."),
        "has_document_number": Noul(
            instructions="Można jednoznacznie ustalić numer zamówienia, faktury lub innego dokumentu."
        ),
        "has_customer_project": Noul(instructions="Można jednoznacznie ustalić klienta lub projekt."),
        "needs_human_review": Noul(
            instructions="Treść jest niejednoznaczna, sprzeczna albo wymaga szczególnej uwagi operatora."
        ),
    }


def analyze_complaint_with_jev(
    db: Session,
    complaint_id: int,
    *,
    client: JevClient | None = None,
) -> JevAssessment:
    complaint = db.scalar(
        select(Complaint)
        .options(
            selectinload(Complaint.messages)
            .selectinload(WhatsAppMessage.attachments)
            .selectinload(Attachment.ocr_result)
        )
        .where(Complaint.id == complaint_id)
    )
    if complaint is None or complaint.merged_into_id is not None:
        raise DraftOperationError("Reklamacja nie istnieje")

    state = build_jev_state(complaint)
    serialized_state = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assessment = JevAssessment(
        complaint_id=complaint.id,
        provider="typesafe",
        model=settings.typesafe_model,
        input_hash=hashlib.sha256(serialized_state.encode()).hexdigest(),
        status="processing",
        answers={},
        usage={},
    )
    db.add(assessment)
    db.flush()

    jev_client = client or TypeSafeClient(
        api_key=settings.typesafe_api_key,
        model=settings.typesafe_model,
        timeout=float(settings.typesafe_timeout_seconds),
    )
    try:
        response = jev_client.system_one(state=state, questions=jev_questions(), model=settings.typesafe_model)
        payload = response.model_dump(mode="json")
        assessment.model = str(payload.get("model") or settings.typesafe_model)
        assessment.answers = dict(payload.get("answers") or {})
        assessment.usage = dict(payload.get("usage") or {})
        assessment.status = "completed"
        assessment.completed_at = datetime.now(timezone.utc)
        db.add(
            ComplaintEvent(
                complaint_id=complaint.id,
                event_type="jev_analysis_completed",
                actor="panel",
                details={"assessment_id": assessment.id, "input_hash": assessment.input_hash},
            )
        )
    except Exception as error:
        assessment.status = "failed"
        assessment.last_error = f"{type(error).__name__}: połączenie z TypeSafe nie powiodło się"
        assessment.completed_at = datetime.now(timezone.utc)
        db.add(
            ComplaintEvent(
                complaint_id=complaint.id,
                event_type="jev_analysis_failed",
                actor="panel",
                details={"assessment_id": assessment.id, "error_type": type(error).__name__},
            )
        )
    return assessment


def answer_rows(assessment: JevAssessment | None) -> list[dict[str, Any]]:
    if assessment is None or assessment.status != "completed":
        return []
    rows = []
    for key, answer in assessment.answers.items():
        answer_type = answer.get("type")
        if answer_type == "noul":
            value = f"{float(answer.get('noul', 0)):.0%}"
            confidence = None
        elif answer_type == "score":
            value = f"{float(answer.get('score', 0)):.2f}"
            confidence = f"{float(answer.get('confidence', 0)):.0%}"
        else:
            value = str(answer.get("choice", "—"))
            confidence = f"{float(answer.get('confidence', 0)):.0%}"
        rows.append({"key": key, "label": QUESTION_LABELS.get(key, key), "value": value, "confidence": confidence})
    return rows
