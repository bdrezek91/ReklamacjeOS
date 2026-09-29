import secrets
from email.message import EmailMessage
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .api import router as api_router
from .config import settings
from .db import get_db
from .models import (
    Attachment,
    Complaint,
    ComplaintStatus,
    EmailDraft,
    EmailOutbox,
    Supplier,
    WhatsAppMessage,
    WhatsAppOutbox,
)
from .services.complaints import (
    WHATSAPP_RESOLUTIONS,
    accept_complaint,
    change_complaint_status,
    enqueue_whatsapp_resolution,
    retry_whatsapp_notification,
    update_complaint_card,
)
from .services.correspondence import (
    assign_supplier,
    enqueue_smtp_email,
    mark_manually_sent,
    prepare_email_draft,
    retry_smtp_email,
    save_supplier,
    selected_attachments,
    update_email_draft,
)
from .services.grouping import DraftOperationError, merge_drafts, split_draft
from .services.jev import analyze_complaint_with_jev, answer_rows

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="ReklamacjeOS", version="0.1.0", docs_url=None, redoc_url=None)
app.include_router(api_router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["root_path"] = settings.root_path


def verify_panel_action_token(action_token: str) -> None:
    if not settings.panel_action_token or not secrets.compare_digest(action_token, settings.panel_action_token):
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
            "accepted_count": status_counts.get(ComplaintStatus.ACCEPTED, 0),
            "ready_count": status_counts.get(ComplaintStatus.READY_TO_SEND, 0),
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
def complaints_by_status(
    status_name: str,
    request: Request,
    q: str = "",
    supplier_id: int | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    mappings = {
        "drafts": (ComplaintStatus.DRAFT, "Drafty"),
        "pending": (ComplaintStatus.PENDING_APPROVAL, "Do akceptacji"),
        "accepted": (ComplaintStatus.ACCEPTED, "Zaakceptowane"),
        "ready": (ComplaintStatus.READY_TO_SEND, "Gotowe do wysłania"),
        "sent": (ComplaintStatus.SENT, "Wysłane"),
        "closed": (ComplaintStatus.CLOSED, "Zamknięte"),
    }
    status_value, title = mappings.get(status_name, (ComplaintStatus.DRAFT, "Drafty"))
    query = (
        select(Complaint)
        .outerjoin(Supplier)
        .options(
            selectinload(Complaint.messages).selectinload(WhatsAppMessage.attachments),
            selectinload(Complaint.supplier),
        )
        .where(Complaint.status == status_value, Complaint.merged_into_id.is_(None))
        .order_by(Complaint.updated_at.desc(), Complaint.id.desc())
    )
    clean_query = q.strip()
    if clean_query:
        pattern = f"%{clean_query}%"
        query = query.where(
            or_(
                Complaint.official_number.ilike(pattern),
                Complaint.draft_number.ilike(pattern),
                Supplier.name.ilike(pattern),
            )
        )
    if supplier_id is not None:
        query = query.where(Complaint.supplier_id == supplier_id)
    complaints = db.scalars(query).unique().all()
    suppliers = db.scalars(select(Supplier).order_by(Supplier.name)).all()
    return templates.TemplateResponse(
        request=request,
        name="complaints.html",
        context={
            "active": status_name,
            "title": title,
            "complaints": complaints,
            "suppliers": suppliers,
            "query": clean_query,
            "selected_supplier_id": supplier_id,
        },
    )


@app.get("/drafts/{complaint_id}", response_class=HTMLResponse)
def draft_detail(complaint_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    complaint = db.scalar(
        select(Complaint)
        .options(
            selectinload(Complaint.messages).selectinload(WhatsAppMessage.attachments),
            selectinload(Complaint.events),
            selectinload(Complaint.merged_into),
            selectinload(Complaint.supplier),
            selectinload(Complaint.jev_assessments),
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
    outboxes = db.scalars(
        select(WhatsAppOutbox).where(WhatsAppOutbox.complaint_id == complaint.id).order_by(WhatsAppOutbox.id)
    ).all()
    acceptance_outbox = next((item for item in outboxes if item.message_kind == "acceptance"), None)
    resolution_outbox = next((item for item in outboxes if item.message_kind == "resolution"), None)
    email_draft = db.scalar(select(EmailDraft).where(EmailDraft.complaint_id == complaint.id))
    email_outbox = db.scalar(select(EmailOutbox).where(EmailOutbox.complaint_id == complaint.id))
    suppliers = db.scalars(select(Supplier).where(Supplier.active.is_(True)).order_by(Supplier.name)).all()
    latest_jev_assessment = max(complaint.jev_assessments, key=lambda item: item.id, default=None)
    return templates.TemplateResponse(
        request=request,
        name="draft_detail.html",
        context={
            "active": {
                ComplaintStatus.DRAFT: "drafts",
                ComplaintStatus.PENDING_APPROVAL: "pending",
                ComplaintStatus.ACCEPTED: "accepted",
                ComplaintStatus.READY_TO_SEND: "ready",
                ComplaintStatus.SENT: "sent",
                ComplaintStatus.CLOSED: "closed",
            }[complaint.status],
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
                ComplaintStatus.ACCEPTED: "Zaakceptowana",
                ComplaintStatus.READY_TO_SEND: "Gotowa do wysłania",
                ComplaintStatus.SENT: "Wysłana",
                ComplaintStatus.CLOSED: "Zamknięta",
            }[complaint.status],
            "card_fields": (
                ("material_product", "Materiał / produkt"),
                ("quantity", "Ilość"),
                ("defect_description", "Opis wady"),
                ("document_number", "Numer zamówienia lub faktury"),
                ("customer_project", "Klient / projekt"),
                ("notes", "Uwagi"),
            ),
            "panel_action_token": settings.panel_action_token,
            "acceptance_outbox": acceptance_outbox,
            "resolution_outbox": resolution_outbox,
            "whatsapp_resolutions": WHATSAPP_RESOLUTIONS,
            "email_draft": email_draft,
            "email_outbox": email_outbox,
            "smtp_configured": settings.smtp_configured,
            "typesafe_configured": settings.typesafe_configured,
            "jev_assessment": latest_jev_assessment,
            "jev_answer_rows": answer_rows(latest_jev_assessment),
            "suppliers": suppliers,
        },
    )


@app.post("/drafts/{complaint_id}/analyze-jev")
def analyze_jev_action(
    complaint_id: int,
    action_token: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_panel_action_token(action_token)
    if not settings.typesafe_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TypeSafe Jev nie jest skonfigurowany",
        )
    try:
        analyze_complaint_with_jev(db, complaint_id)
        db.commit()
    except DraftOperationError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return RedirectResponse(f"{settings.root_path}/drafts/{complaint_id}", status_code=status.HTTP_303_SEE_OTHER)


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
    return RedirectResponse(f"{settings.root_path}/drafts/{complaint_id}", status_code=status.HTTP_303_SEE_OTHER)


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
    return RedirectResponse(f"{settings.root_path}/drafts/{complaint_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/drafts/{complaint_id}/accept")
def accept_complaint_action(
    complaint_id: int,
    action_token: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_panel_action_token(action_token)
    try:
        accept_complaint(
            db,
            complaint_id,
            actor="panel",
            group_id=settings.whatsapp_group_id,
        )
        db.commit()
    except DraftOperationError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return RedirectResponse(f"{settings.root_path}/drafts/{complaint_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/drafts/{complaint_id}/retry-whatsapp")
def retry_whatsapp_action(
    complaint_id: int,
    action_token: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_panel_action_token(action_token)
    try:
        retry_whatsapp_notification(db, complaint_id, actor="panel")
        db.commit()
    except DraftOperationError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return RedirectResponse(f"{settings.root_path}/drafts/{complaint_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/drafts/{complaint_id}/whatsapp-resolution")
def whatsapp_resolution_action(
    complaint_id: int,
    resolution: str = Form(...),
    action_token: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_panel_action_token(action_token)
    try:
        enqueue_whatsapp_resolution(
            db,
            complaint_id,
            resolution,
            actor="panel",
            group_id=settings.whatsapp_group_id,
        )
        db.commit()
    except DraftOperationError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return RedirectResponse(f"{settings.root_path}/drafts/{complaint_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/drafts/{complaint_id}/retry-whatsapp-resolution")
def retry_whatsapp_resolution_action(
    complaint_id: int,
    action_token: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_panel_action_token(action_token)
    try:
        retry_whatsapp_notification(db, complaint_id, actor="panel", message_kind="resolution")
        db.commit()
    except DraftOperationError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return RedirectResponse(f"{settings.root_path}/drafts/{complaint_id}", status_code=status.HTTP_303_SEE_OTHER)


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


@app.get("/suppliers", response_class=HTMLResponse)
def suppliers_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    suppliers = db.scalars(select(Supplier).order_by(Supplier.active.desc(), Supplier.name)).all()
    return templates.TemplateResponse(
        request=request,
        name="suppliers.html",
        context={
            "active": "suppliers",
            "suppliers": suppliers,
            "panel_action_token": settings.panel_action_token,
        },
    )


@app.post("/suppliers/save")
def save_supplier_action(
    action_token: str = Form(...),
    supplier_id: int | None = Form(default=None),
    name: str = Form(...),
    email: str = Form(...),
    contact_person: str = Form(default=""),
    phone: str = Form(default=""),
    notes: str = Form(default=""),
    active: bool = Form(default=False),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_panel_action_token(action_token)
    try:
        save_supplier(
            db,
            supplier_id=supplier_id,
            name=name,
            email=email,
            contact_person=contact_person,
            phone=phone,
            notes=notes,
            active=active,
        )
        db.commit()
    except (DraftOperationError, IntegrityError) as error:
        db.rollback()
        detail = str(error) if isinstance(error, DraftOperationError) else "Nie udało się zapisać dostawcy"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from error
    return RedirectResponse(f"{settings.root_path}/suppliers", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/drafts/{complaint_id}/supplier")
def assign_supplier_action(
    complaint_id: int,
    supplier_id: int = Form(...),
    action_token: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_panel_action_token(action_token)
    try:
        assign_supplier(db, complaint_id, supplier_id, actor="panel")
        db.commit()
    except DraftOperationError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return RedirectResponse(f"{settings.root_path}/drafts/{complaint_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/drafts/{complaint_id}/prepare-email")
def prepare_email_action(
    complaint_id: int,
    action_token: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_panel_action_token(action_token)
    try:
        prepare_email_draft(db, complaint_id, actor="panel")
        db.commit()
    except DraftOperationError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return RedirectResponse(f"{settings.root_path}/drafts/{complaint_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/drafts/{complaint_id}/email-draft")
def update_email_action(
    complaint_id: int,
    action_token: str = Form(...),
    recipient: str = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_panel_action_token(action_token)
    try:
        update_email_draft(
            db,
            complaint_id,
            recipient=recipient,
            subject=subject,
            body=body,
            actor="panel",
        )
        db.commit()
    except DraftOperationError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return RedirectResponse(f"{settings.root_path}/drafts/{complaint_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/drafts/{complaint_id}/email.eml")
def download_email(complaint_id: int, db: Session = Depends(get_db)) -> Response:
    complaint = db.get(Complaint, complaint_id)
    draft = db.scalar(select(EmailDraft).where(EmailDraft.complaint_id == complaint_id))
    if (
        complaint is None
        or draft is None
        or complaint.status
        not in {
            ComplaintStatus.READY_TO_SEND,
            ComplaintStatus.SENT,
            ComplaintStatus.CLOSED,
        }
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Email draft not found")

    message = EmailMessage()
    message["To"] = draft.recipient
    message["Subject"] = draft.subject
    message["X-Unsent"] = "1"
    message.set_content(draft.body)
    root = settings.data_root.resolve()
    for attachment in selected_attachments(db, complaint.id):
        path = (settings.data_root / attachment.storage_path).resolve()
        if root not in path.parents or not path.is_file():
            continue
        main_type, sub_type = (attachment.mime_type.split("/", 1) + ["octet-stream"])[:2]
        message.add_attachment(
            path.read_bytes(),
            maintype=main_type,
            subtype=sub_type,
            filename=attachment.original_filename,
        )
    safe_number = (complaint.official_number or complaint.draft_number).replace("/", "-")
    return Response(
        content=message.as_bytes(),
        media_type="message/rfc822",
        headers={"Content-Disposition": f'attachment; filename="reklamacja-{safe_number}.eml"'},
    )


@app.post("/drafts/{complaint_id}/mark-sent")
def mark_sent_action(
    complaint_id: int,
    action_token: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_panel_action_token(action_token)
    try:
        mark_manually_sent(db, complaint_id, actor="panel")
        db.commit()
    except DraftOperationError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return RedirectResponse(f"{settings.root_path}/drafts/{complaint_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/drafts/{complaint_id}/send-smtp")
def send_smtp_action(
    complaint_id: int,
    action_token: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_panel_action_token(action_token)
    if not settings.smtp_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Skrzynka SMTP nie jest skonfigurowana",
        )
    try:
        enqueue_smtp_email(
            db,
            complaint_id,
            sender=settings.smtp_from_address,
            actor="panel",
        )
        db.commit()
    except DraftOperationError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return RedirectResponse(f"{settings.root_path}/drafts/{complaint_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/drafts/{complaint_id}/retry-smtp")
def retry_smtp_action(
    complaint_id: int,
    action_token: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    verify_panel_action_token(action_token)
    try:
        retry_smtp_email(db, complaint_id, actor="panel")
        db.commit()
    except DraftOperationError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return RedirectResponse(f"{settings.root_path}/drafts/{complaint_id}", status_code=status.HTTP_303_SEE_OTHER)
