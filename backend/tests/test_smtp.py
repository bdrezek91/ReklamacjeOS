from email.message import EmailMessage

from sqlalchemy import select

from app import email_worker
from app.config import settings
from app.models import Complaint, ComplaintStatus, EmailDraft, EmailOutbox

from .test_ingest import make_client


def test_queues_immutable_smtp_snapshot(tmp_path):
    client, session = make_client(tmp_path)
    object.__setattr__(settings, "smtp_host", "smtp.example.test")
    object.__setattr__(settings, "smtp_enabled", True)
    object.__setattr__(settings, "smtp_from_address", "reklamacje@example.test")

    complaint = Complaint(
        draft_number="DRAFT-SMTP",
        official_number="R/99/09/2026",
        status=ComplaintStatus.READY_TO_SEND,
    )
    session.add(complaint)
    session.flush()
    session.add(
        EmailDraft(
            complaint_id=complaint.id,
            recipient="odbiorca@example.test",
            subject="Reklamacja testowa",
            body="Treść testowa",
        )
    )
    session.commit()

    queued = client.post(
        f"/drafts/{complaint.id}/send-smtp",
        data={"action_token": "panel-test-token"},
        follow_redirects=False,
    )
    assert queued.status_code == 303
    item = session.scalar(select(EmailOutbox).where(EmailOutbox.complaint_id == complaint.id))
    assert item.status == "pending"
    assert item.sender == "reklamacje@example.test"
    assert item.recipient == "odbiorca@example.test"
    assert item.subject == "Reklamacja testowa"
    assert item.message_id.endswith("@example.test>")

    duplicate = client.post(
        f"/drafts/{complaint.id}/send-smtp",
        data={"action_token": "panel-test-token"},
        follow_redirects=False,
    )
    assert duplicate.status_code == 400
    assert len(session.scalars(select(EmailOutbox)).all()) == 1


def test_worker_builds_tls_message_without_exposing_credentials(tmp_path, monkeypatch):
    attachment_path = tmp_path / "photo.jpg"
    attachment_content = b"photo-content"
    attachment_path.write_bytes(attachment_content)
    object.__setattr__(settings, "data_root", tmp_path)
    object.__setattr__(settings, "smtp_host", "smtp.example.test")
    object.__setattr__(settings, "smtp_enabled", True)
    object.__setattr__(settings, "smtp_port", 587)
    object.__setattr__(settings, "smtp_username", "smtp-user")
    object.__setattr__(settings, "smtp_password", "smtp-secret")
    object.__setattr__(settings, "smtp_from_name", "Reklamacje DAMPOL")
    object.__setattr__(settings, "smtp_starttls", True)
    object.__setattr__(settings, "smtp_ssl", False)

    calls: list[object] = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def ehlo(self):
            calls.append("ehlo")

        def starttls(self, context):
            calls.append("starttls")

        def login(self, username, password):
            calls.append(("login", username, password))

        def send_message(self, message: EmailMessage):
            calls.append(("message", message))
            return {}

        def quit(self):
            calls.append("quit")

    monkeypatch.setattr(email_worker.smtplib, "SMTP", FakeSMTP)
    item = EmailOutbox(
        id=1,
        complaint_id=1,
        recipient="odbiorca@example.test",
        sender="reklamacje@example.test",
        subject="Reklamacja R/01/09/2026",
        body="Treść reklamacji",
        message_id="<test@example.test>",
        status="processing",
        attachments=[
            {
                "id": 1,
                "filename": "wada.jpg",
                "storage_path": "photo.jpg",
                "mime_type": "image/jpeg",
                "sha256": "7b4b48ede5a5b80f23e189c064c3822d55c80b97bb18107b8cd16064e601e4cb",
                "size_bytes": len(attachment_content),
            }
        ],
    )

    email_worker.deliver(item)

    message = next(call[1] for call in calls if isinstance(call, tuple) and call[0] == "message")
    assert message["To"] == "odbiorca@example.test"
    assert message["Message-ID"] == "<test@example.test>"
    assert [part.get_filename() for part in message.iter_attachments()] == ["wada.jpg"]
    assert "starttls" in calls
    assert ("login", "smtp-user", "smtp-secret") in calls
