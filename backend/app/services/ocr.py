from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Attachment, ComplaintEvent, OcrResult, WhatsAppMessage
from .grouping import DraftOperationError

SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/tiff", "image/bmp"}
MAX_OCR_TEXT_LENGTH = 20_000


def queue_complaint_ocr(db: Session, complaint_id: int, *, actor: str) -> int:
    attachments = db.scalars(
        select(Attachment)
        .join(WhatsAppMessage, Attachment.whatsapp_message_id == WhatsAppMessage.id)
        .where(WhatsAppMessage.complaint_id == complaint_id, Attachment.mime_type.in_(SUPPORTED_IMAGE_TYPES))
        .order_by(Attachment.id)
    ).all()
    if not attachments:
        raise DraftOperationError("Reklamacja nie zawiera obsługiwanych zdjęć")

    queued = 0
    for attachment in attachments:
        result = db.scalar(select(OcrResult).where(OcrResult.attachment_id == attachment.id))
        if result is None:
            db.add(
                OcrResult(
                    attachment_id=attachment.id,
                    engine="tesseract",
                    language=settings.ocr_language,
                    status="pending",
                    raw_text="",
                )
            )
            queued += 1
        elif result.status == "failed":
            result.status = "pending"
            result.last_error = None
            result.claimed_at = None
            queued += 1

    if queued == 0:
        raise DraftOperationError("OCR tych zdjęć został już zlecony")
    db.add(
        ComplaintEvent(
            complaint_id=complaint_id,
            event_type="ocr_queued",
            actor=actor,
            details={"attachment_count": queued},
        )
    )
    db.flush()
    return queued


def save_corrected_ocr_text(
    db: Session,
    complaint_id: int,
    attachment_id: int,
    text: str,
    *,
    actor: str,
) -> OcrResult:
    result = db.scalar(
        select(OcrResult)
        .join(Attachment, OcrResult.attachment_id == Attachment.id)
        .join(WhatsAppMessage, Attachment.whatsapp_message_id == WhatsAppMessage.id)
        .where(
            OcrResult.attachment_id == attachment_id,
            WhatsAppMessage.complaint_id == complaint_id,
            OcrResult.status == "completed",
        )
    )
    if result is None:
        raise DraftOperationError("Wynik OCR nie jest dostępny")
    clean_text = text.strip()[:MAX_OCR_TEXT_LENGTH]
    result.corrected_text = clean_text
    result.reviewed_at = datetime.now().astimezone()
    db.add(
        ComplaintEvent(
            complaint_id=complaint_id,
            event_type="ocr_text_reviewed",
            actor=actor,
            details={"attachment_id": attachment_id, "text_length": len(clean_text)},
        )
    )
    return result
