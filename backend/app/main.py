from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .api import router as api_router
from .db import get_db
from .models import Attachment, Complaint, ComplaintStatus, WhatsAppMessage

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="ReklamacjeOS", version="0.1.0", docs_url=None, redoc_url=None)
app.include_router(api_router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


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
        .options(selectinload(WhatsAppMessage.attachments))
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
        "pending": (ComplaintStatus.PENDING_APPROVAL, "Do akceptacji"),
        "sent": (ComplaintStatus.SENT, "Wysłane"),
        "closed": (ComplaintStatus.CLOSED, "Zamknięte"),
    }
    status_value, title = mappings.get(status_name, (ComplaintStatus.DRAFT, "Drafty"))
    complaints = db.scalars(
        select(Complaint).where(Complaint.status == status_value).order_by(Complaint.created_at.desc())
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="complaints.html",
        context={"active": status_name, "title": title, "complaints": complaints},
    )
