"""Archive an already-SMTP-accepted ReklamacjeOS message to the sender's IMAP Sent folder.

This module never sends SMTP mail. Search by the existing stable Message-ID first:
the IMAP APPEND is skipped if that Message-ID is already in Sent.
"""
import imaplib
import re
from email.utils import format_datetime
from datetime import datetime

from sqlalchemy import select

from ..config import settings
from ..db import SessionLocal
from ..models import EmailOutbox, EmailSent, SupplierReplyOutbox
from ..email_worker import build_message


def _sent_folder(conn):
    status, rows = conn.list()
    if status != "OK":
        raise RuntimeError("Cannot enumerate IMAP mailboxes")
    fallback = None
    for raw in rows or ():
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        match = re.search(r'(?:"([^"]+)"|([^\s]+))\s*$', line.strip())
        name = (match.group(1) or match.group(2)) if match else None
        if not name:
            continue
        if "\\sent" in line.lower():
            return name
        if "sent" in name.lower() or "wys" in name.lower():
            fallback = fallback or name
    if not fallback:
        raise RuntimeError("IMAP Sent folder not found")
    return fallback


def archive_outbox(outbox_id, *, supplier_reply=False):
    with SessionLocal() as db:
        item = db.get(SupplierReplyOutbox if supplier_reply else EmailOutbox, outbox_id)
        if item is None or item.status != "sent" or not item.sent_at:
            raise RuntimeError("Outbox must have confirmed SMTP acceptance")
        record = db.scalar(select(EmailSent).where(EmailSent.message_id == item.message_id))
        if record is None:
            raise RuntimeError("EmailSent receipt not found")
        if (record.smtp_result or {}).get("sent_copy", {}).get("status") == "saved":
            return {"status": "already_recorded"}
        message = build_message(item)
        if message["Date"]:
            message.replace_header("Date", format_datetime(item.sent_at))
        folder = None
        conn = imaplib.IMAP4_SSL(settings.smtp_host, 993, timeout=20)
        try:
            conn.login(settings.smtp_username, settings.smtp_password)
            folder = _sent_folder(conn)
            status, _ = conn.select(folder, readonly=True)
            if status != "OK":
                raise RuntimeError("Cannot select IMAP Sent folder")
            status, ids = conn.search(None, "HEADER", "Message-ID", f'"{item.message_id}"')
            if status != "OK":
                raise RuntimeError("IMAP Message-ID search failed; refusing APPEND")
            exists = bool(ids and ids[0].strip())
            conn.close()
            if not exists:
                status, _ = conn.append(
                    folder,
                    "\\Seen",
                    imaplib.Time2Internaldate(item.sent_at.timestamp()),
                    message.as_bytes(),
                )
                if status != "OK":
                    raise RuntimeError("IMAP APPEND failed or response not confirmed")
        finally:
            try:
                conn.logout()
            except Exception:
                pass
        result = dict(record.smtp_result or {})
        result["sent_copy"] = {"status": "saved", "folder": folder}
        record.smtp_result = result
        db.commit()
        return {"status": "already_present" if exists else "appended", "folder": folder}


if __name__ == "__main__":
    import sys
    print(archive_outbox(int(sys.argv[1])))
