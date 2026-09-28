import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .api import router as api_router
from .config import settings
from .db import get_db
from .models import Attachment, Complaint, ComplaintStatus, WhatsAppMessage
from .services.complaints import change_complaint_status, update_complaint_card
from .services.grouping import DraftOperationError, merge_drafts, split_draft

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="ReklamacjeOS", version="0.1.0", docs_url=None, redoc_url=None)
app.include_router(api_router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["root_path"] = settings.root_path


def verify_panel_action_token(action_token: str) -> None:
    if not settings.panel_action_token or not secrets.compare_digest(
        action_token, settings.panel_action_token
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid panel action token")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    message_count = db.scalar(select(func.count()).select_from(WhatsAppMessage)) or 0
    attachment_count = db.scalar(select(func.count()).select_from(Attachment)) or 0
    status_counts = dict(db.execute(select(Complaint.status, func.count()).group_by(Complaint.status)).all())
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "active": "dashboard",
            "message_count": message_count,
            "attachment_count": attachment_count,
            "draft_count": status_counts.get(ComplaintStatus.DRAFT, 0),
            "pending_count": status_counts.get(ComplaintStatus.PENDING_APPROVAL, 0),
            "sent_count": status_counts.get(ComplaintStatus.SENT, 0),
        },
    )


@app.get("/inbox", response_class=HTMLResponse)
def inbox(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    messages = db.scalars(
        select(WhatsAppMessage)
        .options(selectinload(WhatsAppMessage.attachments), selectinload(WhatsAppMessage.complaint))
        .order_by(WhatsAppMessage.source_timestamp.desc())
        .limit(250)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="inbox.html",
        context={"active": "inbox", "messages": messages},
    )


@app.get("/complaints/{status_name}", response_class=HTMLResponse)
def complaints_by_status(status_name: str, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    mappings = {
        "drafts": (ComplaintStatus.DRAFT, "Drafty"),
        "pending": (ComplaintStatus.PENDING_APPROVAL, "Do akceptacji"),
        "sent": (ComplaintStatus.SENT, "Wysłane"),
        "closed": (ComplaintStatus.CLOSED, "Zamknięte"),
    }
    status_value, title = mappings.get(status_name, (ComplaintStatus.DRAFT, "Drafty"))
    complaints = db.scalars(
        select(Complaint)
        .options(selectinload(Complaint.messages).selectinload(WhatsAppMessage.attachments))
        .where(Complaint.status == status_value, Complaint.merged_into_id.is_(None))
        .order_by(Complaint.updated_at.desc(), Complaint.id.desc())
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="complaints.html",
        context={"active": status_name, "title": title, "complaints": complaints},
    )


@app.get("/drafts/{complaint_id}", response_class=HTMLResponse)
def draft_detail(complaint_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    complaint = db.scalar(
        select(Complaint)
        .options(
            selectinload(Complaint.messages).selectinload(WhatsAppMessage.attachments),
            selectinload(Complaint.events),
            selectinload(Complaint.merged_into),
        )
        .where(Complaint.id == complaint_id)
    )
    if complaint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")

    targets = db.scalars(
        select(Complaint)
        .where(
            Complaint.status == ComplaintStatus.DRAFT,
            Complaint.merged_into_id.is_(None),
            Complaint.id != complaint.id,
        )
        .order_by(Complaint.updated_at.desc(), Complaint.id.desc())
    ).all()
    messages = sorted(complaint.messages, key=lambda message: (message.source_timestamp, message.id))
    events = sorted(complaint.events, key=lambda event: (event.created_at, event.id), reverse=True)
    attachment_count = sum(len(message.attachments) for message in messages)
    attachments = [attachment for message in messages for attachment in message.attachments]
    return templates.TemplateResponse(
        request=request,
        name="draft_detail.html",
        context={
            "active": "drafts",
            "complaint": complaint,
            "messages": messages,
            "events": events,
            "targets": targets,
            "attachment_count": attachment_count,
            "attachments": attachments,
            "card_data": complaint.approved_data or {},
            "status_label": {
                ComplaintStatus.DRAFT: "Draft",
                ComplaintStatus.PENDING_APPROVAL: "Do akceptacji",
                ComplaintStatus.SENT: "Wysłana",
                ComplaintStatus.CLOSED: "Zamknięta",
            }[complaint.status],
            "card_fields": (
                ("supplier", "Dostawca"),
                ("material_product", "Materiał / produkt"),
                ("quantity", "Ilość"),
                ("defect_description", "Opis wady"),
                ("document_number", "Numer zamówienia lub faktury"),
                ("customer_project", "Klient / projekt"),
                ("notes", "Uwagi"),
            ),
            "panel_action_token": settings.panel_action_token,
        },
    )


@app.post("/drafts/{complaint_id}/merge")
def merge_draft_action(
    complaint_id: int,
    target_complaint_id: int = Form(...),
    action_token: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_panel_action_token(action_token)
    try:
        target = merge_drafts(db, complaint_id, target_complaint_id, actor="panel")
        db.commit()
    except DraftOperationError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return RedirectResponse(f"{settings.root_path}/drafts/{target.id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/drafts/{complaint_id}/card")
def update_complaint_card_action(
    complaint_id: int,
    action_token: str = Form(...),
    supplier: str = Form(default=""),
    material_product: str = Form(default=""),
    quantity: str = Form(default=""),
    defect_description: str = Form(default=""),
    document_number: str = Form(default=""),
    customer_project: str = Form(default=""),
    notes: str = Form(default=""),
    attachment_ids: list[int] | None = Form(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_panel_action_token(action_token)
    try:
        update_complaint_card(
            db,
            complaint_id,
            values={
                "supplier": supplier,
                "material_product": material_product,
                "quantity": quantity,
                "defect_description": defect_description,
                "document_number": document_number,
                "customer_project": customer_project,
                "notes": notes,
            },
            included_attachment_ids=attachment_ids or [],
            actor="panel",
        )
        db.commit()
    except DraftOperationError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return RedirectResponse(
        f"{settings.root_path}/drafts/{complaint_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@app.post("/drafts/{complaint_id}/status")
def change_complaint_status_action(
    complaint_id: int,
    target_status: str = Form(...),
    action_token: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_panel_action_token(action_token)
    try:
        parsed_status = ComplaintStatus(target_status)
        change_complaint_status(db, complaint_id, parsed_status, actor="panel")
        db.commit()
    except (DraftOperationError, ValueError) as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return RedirectResponse(
        f"{settings.root_path}/drafts/{complaint_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@app.post("/drafts/{complaint_id}/split")
def split_draft_action(
    complaint_id: int,
    message_ids: list[int] = Form(...),
    action_token: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_panel_action_token(action_token)
    try:
        target = split_draft(db, complaint_id, message_ids, actor="panel")
        db.commit()
    except DraftOperationError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return RedirectResponse(f"{settings.root_path}/drafts/{target.id}", status_code=status.HTTP_303_SEE_OTHER)
