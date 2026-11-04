import hashlib
import json
import signal
import smtplib
import ssl
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from pathlib import Path

from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .models import Complaint, ComplaintEvent, ComplaintStatus, EmailOutbox, EmailSent, SupplierReplyOutbox
from .services.signature import set_message_content

RUNNING = True
READY_FILE = Path("/tmp/email-worker-ready")


class AmbiguousSendError(RuntimeError):
    pass


def log(message: str, **metadata: object) -> None:
    print(
        json.dumps(
            {"time": datetime.now().astimezone().isoformat(), "message": message, **metadata},
            ensure_ascii=False,
        ),
        flush=True,
    )


def claim_next() -> EmailOutbox | None:
    with SessionLocal.begin() as db:
        query = select(EmailOutbox).where(EmailOutbox.status == "pending").order_by(EmailOutbox.id)
        if db.get_bind().dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        item = db.scalar(query.limit(1))
        if item is None:
            return None
        item.status = "processing"
        item.attempt_count += 1
        item.claimed_at = datetime.now().astimezone()
        db.flush()
        db.expunge(item)
        return item


def attachment_bytes(metadata: dict[str, object]) -> bytes:
    relative_path = str(metadata["storage_path"])
    path = (settings.data_root / relative_path).resolve()
    root = settings.data_root.resolve()
    if root not in path.parents or not path.is_file():
        raise FileNotFoundError(f"Brak załącznika id={metadata.get('id')}")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != metadata["sha256"]:
        raise ValueError(f"Niezgodna suma kontrolna załącznika id={metadata.get('id')}")
    return content


def build_message(item: EmailOutbox | SupplierReplyOutbox) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((settings.smtp_from_name, item.sender))
    message["To"] = item.recipient
    message["Subject"] = item.subject
    message["Message-ID"] = item.message_id
    message["Date"] = formatdate(localtime=True)
    if isinstance(item, SupplierReplyOutbox):
        message["In-Reply-To"] = item.in_reply_to
        message["References"] = item.references
    set_message_content(message, item.body)
    for metadata in getattr(item, "attachments", []):
        mime_type = str(metadata.get("mime_type") or "application/octet-stream")
        main_type, sub_type = (mime_type.split("/", 1) + ["octet-stream"])[:2]
        message.add_attachment(
            attachment_bytes(metadata),
            maintype=main_type,
            subtype=sub_type,
            filename=str(metadata.get("filename") or "zalacznik"),
        )
    return message


def deliver(item: EmailOutbox | SupplierReplyOutbox) -> None:
    message = build_message(item)
    context = ssl.create_default_context()
    smtp_class = smtplib.SMTP_SSL if settings.smtp_ssl else smtplib.SMTP
    server = (
        smtp_class(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
            context=context,
        )
        if settings.smtp_ssl
        else smtp_class(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
        )
    )
    try:
        server.ehlo()
        if settings.smtp_starttls and not settings.smtp_ssl:
            server.starttls(context=context)
            server.ehlo()
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        try:
            refused = server.send_message(message)
        except (
            smtplib.SMTPRecipientsRefused,
            smtplib.SMTPSenderRefused,
            smtplib.SMTPDataError,
        ):
            raise
        except Exception as error:
            raise AmbiguousSendError(str(error)) from error
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
    finally:
        try:
            server.quit()
        except Exception:
            pass


def mark_sent(item_id: int) -> None:
    with SessionLocal.begin() as db:
        item = db.scalar(select(EmailOutbox).where(EmailOutbox.id == item_id).with_for_update())
        if item is None or item.status != "processing":
            return
        complaint = db.get(Complaint, item.complaint_id)
        now = datetime.now().astimezone()
        item.status = "sent"
        item.sent_at = now
        item.last_error = None
        db.add(
            EmailSent(
                complaint_id=item.complaint_id,
                recipient=item.recipient,
                subject=item.subject,
                body=item.body,
                attachments=item.attachments,
                message_id=item.message_id,
                smtp_result={"mode": "smtp", "status": "accepted"},
                sent_at=now,
            )
        )
        if complaint is not None:
            complaint.status = ComplaintStatus.SENT
            db.add(
                ComplaintEvent(
                    complaint_id=complaint.id,
                    event_type="smtp_send_succeeded",
                    actor="email-worker",
                    details={"outbox_id": item.id, "message_id": item.message_id},
                )
            )


def mark_error(item_id: int, error: Exception, *, ambiguous: bool) -> None:
    with SessionLocal.begin() as db:
        item = db.scalar(select(EmailOutbox).where(EmailOutbox.id == item_id).with_for_update())
        if item is None or item.status != "processing":
            return
        item.status = "unknown" if ambiguous else "failed"
        item.last_error = str(error)[:1000]
        db.add(
            ComplaintEvent(
                complaint_id=item.complaint_id,
                event_type="smtp_send_unknown" if ambiguous else "smtp_send_failed",
                actor="email-worker",
                details={"outbox_id": item.id, "error": item.last_error},
            )
        )



def claim_next_supplier_reply() -> SupplierReplyOutbox | None:
    with SessionLocal.begin() as db:
        query = select(SupplierReplyOutbox).where(SupplierReplyOutbox.status == "pending").order_by(SupplierReplyOutbox.id)
        if db.get_bind().dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        item = db.scalar(query.limit(1))
        if item is None:
            return None
        item.status = "processing"
        item.attempt_count += 1
        item.claimed_at = datetime.now().astimezone()
        db.flush()
        db.expunge(item)
        return item


def mark_supplier_reply_sent(item_id: int) -> None:
    with SessionLocal.begin() as db:
        item = db.scalar(
            select(SupplierReplyOutbox).where(SupplierReplyOutbox.id == item_id).with_for_update()
        )
        if item is None or item.status != "processing":
            return
        now = datetime.now().astimezone()
        item.status = "sent"
        item.sent_at = now
        item.last_error = None
        db.add(EmailSent(
            complaint_id=item.complaint_id, recipient=item.recipient,
            subject=item.subject, body=item.body, attachments=[],
            message_id=item.message_id,
            smtp_result={"mode":"supplier_reply","status":"accepted","discount_percent":item.discount_percent},
            sent_at=now,
        ))
        db.add(ComplaintEvent(
            complaint_id=item.complaint_id,
            event_type="supplier_offer_acceptance_sent",
            actor="email-worker",
            details={
                "source_id":item.reply_to_source_id,
                "discount_percent":item.discount_percent,
                "reply_outbox_id":item.id,
                "message_id":item.message_id,
            },
        ))


def mark_supplier_reply_error(item_id: int, error: Exception, *, ambiguous: bool) -> None:
    with SessionLocal.begin() as db:
        item = db.scalar(
            select(SupplierReplyOutbox).where(SupplierReplyOutbox.id == item_id).with_for_update()
        )
        if item is None or item.status != "processing":
            return
        item.status = "unknown" if ambiguous else "failed"
        item.last_error = str(error)[:1000]
        db.add(ComplaintEvent(
            complaint_id=item.complaint_id,
            event_type="supplier_offer_acceptance_unknown" if ambiguous else "supplier_offer_acceptance_failed",
            actor="email-worker",
            details={"source_id":item.reply_to_source_id,"reply_outbox_id":item.id,"error_type":type(error).__name__},
        ))

def stop_worker(*_: object) -> None:
    global RUNNING
    RUNNING = False


def main() -> None:
    signal.signal(signal.SIGTERM, stop_worker)
    signal.signal(signal.SIGINT, stop_worker)
    READY_FILE.write_text("ready\n")
    if not settings.smtp_configured:
        log("Worker SMTP oczekuje na konfigurację skrzynki nadawczej")

    while RUNNING:
        if not settings.smtp_configured:
            time.sleep(min(settings.email_worker_interval_seconds, 30))
            continue
        item = claim_next()
        supplier_reply = False
        if item is None:
            item = claim_next_supplier_reply()
            supplier_reply = item is not None
        if item is None:
            time.sleep(settings.email_worker_interval_seconds)
            continue
        try:
            deliver(item)
        except AmbiguousSendError as error:
            if supplier_reply:
                mark_supplier_reply_error(item.id, error, ambiguous=True)
            else:
                mark_error(item.id, error, ambiguous=True)
            log("Niejednoznaczny wynik wysyłki SMTP", outbox_id=item.id, supplier_reply=supplier_reply)
        except Exception as error:
            if supplier_reply:
                mark_supplier_reply_error(item.id, error, ambiguous=False)
            else:
                mark_error(item.id, error, ambiguous=False)
            log("Wysyłka SMTP nie powiodła się", outbox_id=item.id, supplier_reply=supplier_reply, error_type=type(error).__name__)
        else:
            if supplier_reply:
                mark_supplier_reply_sent(item.id)
            else:
                mark_sent(item.id)
            log("Serwer SMTP przyjął wiadomość", outbox_id=item.id, supplier_reply=supplier_reply)
            # A raw SMTP submission does not populate Outlook's Sent folder.
            # Archiving is independent of delivery and must never trigger SMTP retry.
            try:
                from .services.sent_copy import archive_outbox
                copy_result = archive_outbox(item.id, supplier_reply=supplier_reply)
                log("Zapisano kopię w folderze Wysłane", outbox_id=item.id, **copy_result)
            except Exception as error:
                log("Nie zapisano kopii w Wysłanych; SMTP już przyjął wiadomość", outbox_id=item.id, error_type=type(error).__name__)

    READY_FILE.unlink(missing_ok=True)
    log("Worker SMTP zatrzymany")


if __name__ == "__main__":
    main()
