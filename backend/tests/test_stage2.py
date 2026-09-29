from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser

from sqlalchemy import select

from app.models import (
    Attachment,
    Complaint,
    ComplaintEvent,
    ComplaintField,
    ComplaintStatus,
    EmailDraft,
    EmailSent,
    Supplier,
    WhatsAppMessage,
    WhatsAppOutbox,
)
from app.services.complaints import allocate_monthly_number

from .test_ingest import make_client, payload


def test_groups_by_author_window_and_quoted_message(tmp_path):
    client, session = make_client(tmp_path)
    headers = {"Authorization": "Bearer test-token"}
    start = datetime(2026, 9, 28, 6, 30, tzinfo=timezone.utc)

    first = client.post(
        "/api/internal/whatsapp/messages",
        data=payload(wa_message_id="first", source_timestamp=start.isoformat()),
        headers=headers,
    )
    close = client.post(
        "/api/internal/whatsapp/messages",
        data=payload(
            wa_message_id="close",
            source_timestamp=(start + timedelta(minutes=9)).isoformat(),
        ),
        headers=headers,
    )
    late = client.post(
        "/api/internal/whatsapp/messages",
        data=payload(
            wa_message_id="late",
            source_timestamp=(start + timedelta(minutes=20)).isoformat(),
        ),
        headers=headers,
    )
    quoted = client.post(
        "/api/internal/whatsapp/messages",
        data=payload(
            wa_message_id="quoted",
            author_id="other-author@lid",
            quoted_message_id="first",
            source_timestamp=(start + timedelta(minutes=30)).isoformat(),
        ),
        headers=headers,
    )

    assert first.json()["complaint_id"] == close.json()["complaint_id"]
    assert len(first.json()["draft_number"]) <= 32
    assert first.json()["official_number"] is None
    assert late.json()["complaint_id"] != first.json()["complaint_id"]
    assert late.json()["official_number"] is None
    assert quoted.json()["complaint_id"] == first.json()["complaint_id"]
    assert quoted.json()["grouping_rule"] == "quoted_message"
    assert len(session.scalars(select(Complaint)).all()) == 2

    draft_list = client.get("/complaints/drafts")
    assert draft_list.status_code == 200
    assert "DRAFT-0001" in draft_list.text
    draft_detail = client.get(f"/drafts/{first.json()['complaint_id']}")
    assert draft_detail.status_code == 200
    assert "panel-test-token" in draft_detail.text

    assert allocate_monthly_number(session, year=2027, month=1) == "R/01/01/2027"
    assert allocate_monthly_number(session, year=2027, month=1) == "R/02/01/2027"
    assert allocate_monthly_number(session, year=2027, month=2) == "R/01/02/2027"


def test_manual_split_and_merge_preserve_messages_and_audit(tmp_path):
    client, session = make_client(tmp_path)
    headers = {"Authorization": "Bearer test-token"}
    start = datetime(2026, 9, 28, 6, 30, tzinfo=timezone.utc)

    for index in range(3):
        response = client.post(
            "/api/internal/whatsapp/messages",
            data=payload(
                wa_message_id=f"message-{index}",
                source_timestamp=(start + timedelta(minutes=index)).isoformat(),
            ),
            headers=headers,
        )
        assert response.status_code == 200

    messages = session.scalars(select(WhatsAppMessage).order_by(WhatsAppMessage.id)).all()
    source_id = messages[0].complaint_id
    split = client.post(
        f"/drafts/{source_id}/split",
        data={"action_token": "panel-test-token", "message_ids": str(messages[-1].id)},
        follow_redirects=False,
    )
    assert split.status_code == 303

    session.expire_all()
    active = session.scalars(select(Complaint).where(Complaint.merged_into_id.is_(None))).all()
    assert len(active) == 2
    target = next(complaint for complaint in active if complaint.id != source_id)
    assert target.official_number is None
    assert session.get(WhatsAppMessage, messages[-1].id).complaint_id == target.id

    merge = client.post(
        f"/drafts/{target.id}/merge",
        data={"action_token": "panel-test-token", "target_complaint_id": str(source_id)},
        follow_redirects=False,
    )
    assert merge.status_code == 303

    session.expire_all()
    assert session.get(Complaint, target.id).merged_into_id == source_id
    assert all(message.complaint_id == source_id for message in session.scalars(select(WhatsAppMessage)))
    event_types = set(session.scalars(select(ComplaintEvent.event_type)).all())
    assert {"messages_split_out", "messages_split_in", "draft_merged", "draft_merged_into"} <= event_types


def test_panel_actions_require_token(tmp_path):
    client, session = make_client(tmp_path)
    complaint = Complaint(draft_number="DRAFT-0001")
    session.add(complaint)
    session.commit()

    response = client.post(
        f"/drafts/{complaint.id}/merge",
        data={"action_token": "wrong", "target_complaint_id": complaint.id},
    )
    assert response.status_code == 403


def test_queues_whatsapp_resolution_without_replacing_acceptance_message(tmp_path):
    client, session = make_client(tmp_path)
    complaint = Complaint(
        draft_number="DRAFT-0001",
        official_number="R/01/09/2026",
        status=ComplaintStatus.ACCEPTED,
    )
    session.add(complaint)
    session.flush()
    session.add(
        WhatsAppOutbox(
            complaint_id=complaint.id,
            group_id="allowed-group@g.us",
            body="Przyjęto reklamację nr R/01/09/2026.",
            message_kind="acceptance",
            status="sent",
        )
    )
    session.commit()

    queued = client.post(
        f"/drafts/{complaint.id}/whatsapp-resolution",
        data={
            "action_token": "panel-test-token",
            "resolution": "use_first_grade_then_return",
        },
        follow_redirects=False,
    )
    assert queued.status_code == 303

    session.expire_all()
    complaint = session.get(Complaint, complaint.id)
    outboxes = session.scalars(
        select(WhatsAppOutbox).where(WhatsAppOutbox.complaint_id == complaint.id).order_by(WhatsAppOutbox.id)
    ).all()
    assert complaint.whatsapp_resolution == "use_first_grade_then_return"
    assert len(outboxes) == 2
    assert outboxes[0].message_kind == "acceptance"
    assert outboxes[1].message_kind == "resolution"
    assert outboxes[1].status == "pending"
    assert outboxes[1].body == (
        "Reklamacja nr R/01/09/2026: Wykorzystać na 1 gatunek w następnych pawilonach i zwracamy do producenta."
    )

    detail = client.get(f"/drafts/{complaint.id}")
    assert detail.status_code == 200
    assert "Nadaj status i wyślij na WhatsApp" not in detail.text
    assert "Wykorzystać na 1 gatunek" in detail.text

    duplicate = client.post(
        f"/drafts/{complaint.id}/whatsapp-resolution",
        data={
            "action_token": "panel-test-token",
            "resolution": "use_first_grade_then_return",
        },
        follow_redirects=False,
    )
    assert duplicate.status_code == 400

    claimed = client.post(
        "/api/internal/whatsapp/outbox/claim",
        headers={"Authorization": "Bearer test-token"},
    )
    assert claimed.status_code == 200
    assert claimed.json()["id"] == outboxes[1].id
    sent = client.post(
        f"/api/internal/whatsapp/outbox/{outboxes[1].id}/sent",
        data={"wa_message_id": "sent-resolution-message"},
        headers={"Authorization": "Bearer test-token"},
    )
    assert sent.status_code == 200
    assert "whatsapp_resolution_sent" in set(session.scalars(select(ComplaintEvent.event_type)).all())


def test_updates_card_gallery_and_status_with_audit(tmp_path):
    client, session = make_client(tmp_path)
    response = client.post(
        "/api/internal/whatsapp/messages",
        data=payload(wa_message_id="card-photo"),
        files={"media": ("wada.jpg", b"card-photo-content", "image/jpeg")},
        headers={"Authorization": "Bearer test-token"},
    )
    complaint_id = response.json()["complaint_id"]
    attachment = session.scalar(select(Attachment))

    update = client.post(
        f"/drafts/{complaint_id}/card",
        data={
            "action_token": "panel-test-token",
            "material_product": "Płyta X",
            "quantity": "4 szt.",
            "defect_description": "Uszkodzone zamki",
            "document_number": "FV/123",
            "customer_project": "Projekt Z",
            "notes": "Pilne",
            "attachment_ids": str(attachment.id),
        },
        follow_redirects=False,
    )
    assert update.status_code == 303

    status_update = client.post(
        f"/drafts/{complaint_id}/status",
        data={"action_token": "panel-test-token", "target_status": "pending_approval"},
        follow_redirects=False,
    )
    assert status_update.status_code == 303

    accept = client.post(
        f"/drafts/{complaint_id}/accept",
        data={"action_token": "panel-test-token"},
        follow_redirects=False,
    )
    assert accept.status_code == 303

    session.expire_all()
    complaint = session.get(Complaint, complaint_id)
    assert complaint.official_number == "R/01/09/2026"
    assert complaint.approved_data["material_product"] == "Płyta X"
    assert complaint.status == ComplaintStatus.ACCEPTED
    assert session.get(Attachment, attachment.id).include_in_email is True
    assert len(session.scalars(select(ComplaintField)).all()) == 6
    event_types = set(session.scalars(select(ComplaintEvent.event_type)).all())
    assert {"complaint_accepted", "complaint_card_updated", "status_changed"} <= event_types

    outbox = session.scalar(select(WhatsAppOutbox))
    assert outbox.status == "pending"
    assert outbox.body == (
        "Przyjęto reklamację nr R/01/09/2026. Proszę opisać reklamowane płyty: reklamacja nr R/01/09/2026."
    )

    claimed = client.post(
        "/api/internal/whatsapp/outbox/claim",
        headers={"Authorization": "Bearer test-token"},
    )
    assert claimed.status_code == 200
    assert claimed.json()["id"] == outbox.id

    failed = client.post(
        f"/api/internal/whatsapp/outbox/{outbox.id}/failed",
        data={"error": "temporary send failure"},
        headers={"Authorization": "Bearer test-token"},
    )
    assert failed.status_code == 200
    retry = client.post(
        f"/drafts/{complaint_id}/retry-whatsapp",
        data={"action_token": "panel-test-token"},
        follow_redirects=False,
    )
    assert retry.status_code == 303
    claimed_again = client.post(
        "/api/internal/whatsapp/outbox/claim",
        headers={"Authorization": "Bearer test-token"},
    )
    assert claimed_again.status_code == 200

    sent = client.post(
        f"/api/internal/whatsapp/outbox/{outbox.id}/sent",
        data={"wa_message_id": "sent-number-message"},
        headers={"Authorization": "Bearer test-token"},
    )
    assert sent.status_code == 200
    session.expire_all()
    assert session.get(WhatsAppOutbox, outbox.id).status == "sent"
    assert (
        client.post(
            "/api/internal/whatsapp/outbox/claim",
            headers={"Authorization": "Bearer test-token"},
        ).status_code
        == 204
    )

    supplier_save = client.post(
        "/suppliers/save",
        data={
            "action_token": "panel-test-token",
            "name": "Paneltech",
            "email": "reklamacje@paneltech.pl",
            "active": "true",
        },
        follow_redirects=False,
    )
    assert supplier_save.status_code == 303
    supplier = session.scalar(select(Supplier).where(Supplier.name == "Paneltech"))

    assignment = client.post(
        f"/drafts/{complaint_id}/supplier",
        data={"action_token": "panel-test-token", "supplier_id": supplier.id},
        follow_redirects=False,
    )
    assert assignment.status_code == 303
    prepare = client.post(
        f"/drafts/{complaint_id}/prepare-email",
        data={"action_token": "panel-test-token"},
        follow_redirects=False,
    )
    assert prepare.status_code == 303

    session.expire_all()
    complaint = session.get(Complaint, complaint_id)
    draft = session.scalar(select(EmailDraft).where(EmailDraft.complaint_id == complaint_id))
    assert complaint.status == ComplaintStatus.READY_TO_SEND
    assert draft.recipient == "reklamacje@paneltech.pl"
    assert "R/01/09/2026" in draft.subject
    assert "Uszkodzone zamki" in draft.body

    update_draft = client.post(
        f"/drafts/{complaint_id}/email-draft",
        data={
            "action_token": "panel-test-token",
            "recipient": "reklamacje@paneltech.pl",
            "subject": "Testowy temat reklamacji",
            "body": "Testowa treść reklamacji",
        },
        follow_redirects=False,
    )
    assert update_draft.status_code == 303

    eml = client.get(f"/drafts/{complaint_id}/email.eml")
    assert eml.status_code == 200
    message = BytesParser(policy=policy.default).parsebytes(eml.content)
    assert message["To"] == "reklamacje@paneltech.pl"
    assert message["Subject"] == "Testowy temat reklamacji"
    assert message["X-Unsent"] == "1"
    assert [part.get_filename() for part in message.iter_attachments()] == ["wada.jpg"]

    marked_sent = client.post(
        f"/drafts/{complaint_id}/mark-sent",
        data={"action_token": "panel-test-token"},
        follow_redirects=False,
    )
    assert marked_sent.status_code == 303
    close = client.post(
        f"/drafts/{complaint_id}/status",
        data={"action_token": "panel-test-token", "target_status": "closed"},
        follow_redirects=False,
    )
    assert close.status_code == 303
    session.expire_all()
    assert session.get(Complaint, complaint_id).status == ComplaintStatus.CLOSED
    assert session.scalar(select(EmailSent)).smtp_result == {"mode": "manual"}

    filtered = client.get(f"/complaints/closed?q=R/01&supplier_id={supplier.id}")
    assert filtered.status_code == 200
    assert "R/01/09/2026" in filtered.text
