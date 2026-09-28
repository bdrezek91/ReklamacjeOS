import json
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .config import settings
from .db import get_db
from .models import Attachment, Complaint, ComplaintEvent, WhatsAppMessage, WhatsAppOutbox
from .services.grouping import assign_message_to_draft
from .services.storage import store_original_media

router = APIRouter()


def verify_bridge_token(authorization: str | None = Header(default=None)) -> None:
    expected = settings.bridge_api_token
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not expected or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bridge token")


@router.post(
    "/api/internal/whatsapp/outbox/claim",
    dependencies=[Depends(verify_bridge_token)],
    response_model=None,
)
def claim_whatsapp_outbox(db: Session = Depends(get_db)) -> dict[str, object] | Response:
    query = select(WhatsAppOutbox).where(WhatsAppOutbox.status == "pending").order_by(WhatsAppOutbox.id)
    if db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    item = db.scalar(query.limit(1))
    if item is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    item.status = "processing"
    item.attempt_count += 1
    item.claimed_at = datetime.now().astimezone()
    db.commit()
    return {"id": item.id, "group_id": item.group_id, "body": item.body}


@router.post("/api/internal/whatsapp/outbox/{item_id}/sent", dependencies=[Depends(verify_bridge_token)])
def mark_whatsapp_outbox_sent(
    item_id: int,
    wa_message_id: str = Form(...),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    item = db.get(WhatsAppOutbox, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outbox item not found")
    if item.status == "sent":
        return {"ok": True}
    if item.status != "processing":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Outbox item is not processing")

    item.status = "sent"
    item.wa_message_id = wa_message_id
    item.sent_at = datetime.now().astimezone()
    item.last_error = None
    db.add(
        ComplaintEvent(
            complaint_id=item.complaint_id,
            event_type="whatsapp_number_sent",
            actor="whatsapp-bridge",
            details={"outbox_id": item.id, "wa_message_id": wa_message_id},
        )
    )
    db.commit()
    return {"ok": True}


@router.post("/api/internal/whatsapp/outbox/{item_id}/failed", dependencies=[Depends(verify_bridge_token)])
def mark_whatsapp_outbox_failed(
    item_id: int,
    error: str = Form(...),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    item = db.get(WhatsAppOutbox, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outbox item not found")
    if item.status != "processing":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Outbox item is not processing")

    item.status = "failed"
    item.last_error = error[:1000]
    db.add(
        ComplaintEvent(
            complaint_id=item.complaint_id,
            event_type="whatsapp_number_failed",
            actor="whatsapp-bridge",
            details={"outbox_id": item.id, "error": item.last_error},
        )
    )
    db.commit()
    return {"ok": True}


@router.post("/api/internal/whatsapp/messages", dependencies=[Depends(verify_bridge_token)])
async def ingest_whatsapp_message(
    wa_message_id: str = Form(...),
    group_id: str = Form(...),
    group_name: str | None = Form(default=None),
    author_id: str | None = Form(default=None),
    author_name: str | None = Form(default=None),
    body: str = Form(default=""),
    message_type: str = Form(...),
    quoted_message_id: str | None = Form(default=None),
    source_timestamp: datetime = Form(...),
    source_payload: str = Form(default="{}"),
    media: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if not settings.whatsapp_group_id:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="WhatsApp group not configured")
    if group_id != settings.whatsapp_group_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Group is not whitelisted")

    existing = db.scalar(select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == wa_message_id))
    if existing:
        complaint = db.get(Complaint, existing.complaint_id) if existing.complaint_id else None
        if existing.complaint_id is None:
            complaint, _ = assign_message_to_draft(db, existing)
            db.commit()
        return {
            "id": existing.id,
            "created": False,
            "complaint_id": existing.complaint_id,
            "draft_number": complaint.draft_number if complaint else None,
            "official_number": complaint.official_number if complaint else None,
        }

    try:
        payload = json.loads(source_payload)
        if not isinstance(payload, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid source_payload JSON")

    message = WhatsAppMessage(
        wa_message_id=wa_message_id,
        group_id=group_id,
        group_name=group_name,
        author_id=author_id,
        author_name=author_name,
        body=body,
        message_type=message_type,
        quoted_message_id=quoted_message_id,
        source_timestamp=source_timestamp,
        source_payload=payload,
    )
    db.add(message)

    try:
        db.flush()
        if media is not None:
            stored = await store_original_media(media, wa_message_id, source_timestamp)
            db.add(
                Attachment(
                    whatsapp_message_id=message.id,
                    original_filename=stored.original_filename,
                    storage_path=stored.relative_path,
                    mime_type=stored.mime_type,
                    size_bytes=stored.size_bytes,
                    sha256=stored.sha256,
                    include_in_email=True,
                )
            )
        complaint, grouping_rule = assign_message_to_draft(db, message)
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == wa_message_id))
        if existing:
            return {"id": existing.id, "created": False}
        raise

    return {
        "id": message.id,
        "created": True,
        "complaint_id": complaint.id,
        "draft_number": complaint.draft_number,
        "official_number": complaint.official_number,
        "grouping_rule": grouping_rule,
    }


@router.get("/api/whatsapp/messages")
def list_whatsapp_messages(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    messages = db.scalars(
        select(WhatsAppMessage)
        .options(selectinload(WhatsAppMessage.attachments))
        .order_by(WhatsAppMessage.source_timestamp.desc())
        .limit(250)
    ).all()
    return [
        {
            "id": message.id,
            "wa_message_id": message.wa_message_id,
            "author_name": message.author_name,
            "body": message.body,
            "message_type": message.message_type,
            "complaint_id": message.complaint_id,
            "source_timestamp": message.source_timestamp,
            "attachments": [
                {
                    "id": attachment.id,
                    "filename": attachment.original_filename,
                    "mime_type": attachment.mime_type,
                    "size_bytes": attachment.size_bytes,
                    "url": f"{settings.root_path}/media/{attachment.id}",
                }
                for attachment in message.attachments
            ],
        }
        for message in messages
    ]


@router.get("/media/{attachment_id}", response_class=FileResponse)
def get_attachment(attachment_id: int, db: Session = Depends(get_db)) -> FileResponse:
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")

    path = (settings.data_root / attachment.storage_path).resolve()
    root = settings.data_root.resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment file not found")

    return FileResponse(path, media_type=attachment.mime_type, filename=attachment.original_filename)
